/* 딜 시뮬레이션 계산 엔진 (GOAL_DEAL_SIM Phase 1, 2026-07-24).

   "이 물건을 낙찰받아 대출 끼고 N개월 뒤 팔면 프로필(개인/매매사업자/법인)별로 세후 얼마가 남는가."
   - 세율의 단일 출처는 data/dealsim_rules.json (원문 검증본 tax_rules.json 의 압축) — 하드코딩 금지.
   - 브라우저(즉시 재계산)와 node(pytest 계약 테스트)에서 같은 코드가 돈다. DOM 접근 금지, 순수함수만.
   - **개인 컬럼은 같은 페이지의 입찰가 시뮬레이터(src/bidsim.py)와 동일 비용모델**(인지세·등기부대비·
     매도중개보수·장특공 포함, 명도비·이자는 양도세 경비 불산입) — 두 존의 숫자가 어긋나면 UX 사고다.
     교차검증 테스트(test_dealsim_crosscheck)가 상호 드리프트를 잡는다.
     단 한 곳 의도적 차이: 인수 보증금은 취득가액 인정(국심 2006서3481 — 원문 검증 확정)이라
     개인 필요경비에 포함한다(bidsim 은 미포함 = 과대 세금 쪽 보수).
   - 사업자·법인은 명도비·이자·중개보수까지 폭넓게 경비 인정(원문 검증 근거).
   - 모름은 0 이 아니다: 인수금액 미상·규제 판정 불가는 warnings 로 그대로 표출한다. */
(function (root) {
  'use strict';

  var MONTH_BANDS = [6, 18, 30]; // 매트릭스 대표값: 1년 미만 / 1~2년 / 2년 이상

  function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi); }

  // ── 취득세 ───────────────────────────────────────────────────────────
  // 매매사업자의 취득세는 개인과 동일 체계(사업자 혜택 없음 — 검증 확정)라 profile 은 개인/법인만 가른다.
  function acquisitionTax(rules, o) {
    var A = rules.acquisition;
    var notes = [];
    if (!o.isHousing) {
      var t = Math.round(o.price * A.non_housing_total);
      return { rate: A.non_housing_total, total: t, surcharged: false,
               breakdown: { main: Math.round(o.price * 0.04), edu: Math.round(o.price * 0.004), farm: Math.round(o.price * 0.002) },
               notes: ['비주택(상가·오피스텔·토지) 일률 4.6% — 면적·주택수 무관'] };
    }
    var rate, surcharged = false;
    if (o.profile === 'corp') {
      rate = A.housing_surcharge.corp; surcharged = true;
      notes.push('법인 주택 취득 일률 12% 중과(공시 1억 이하 등 예외 제외)');
    } else {
      var n = o.housesAfter; // 취득 후 세대 주택 수
      var std = o.price <= 600000000 ? 0.01
        : o.price <= 900000000 ? clamp(o.price * 2 / 300000000 - 3, 1, 3) / 100
        : 0.03;
      if (o.adjusted) {
        if (n >= 3) { rate = 0.12; surcharged = true; }
        else if (n === 2) { rate = 0.08; surcharged = true; notes.push('일시적 2주택(기한 내 종전주택 처분)이면 표준세율(' + (std * 100).toFixed(1) + '%) — 중과 기준으로 계산'); }
        else rate = std;
      } else {
        if (n >= 4) { rate = 0.12; surcharged = true; }
        else if (n === 3) { rate = 0.08; surcharged = true; }
        else rate = std;
      }
    }
    var main = Math.round(o.price * rate);
    var edu = Math.round(o.price * (surcharged ? 0.004 : rate * 0.1));
    var farm = 0;
    if (o.areaM2 > 85) {
      farm = Math.round(o.price * (surcharged ? (rate >= 0.12 ? 0.010 : 0.006) : 0.002));
    }
    return { rate: rate, total: main + edu + farm, surcharged: surcharged,
             breakdown: { main: main, edu: edu, farm: farm }, notes: notes };
  }

  // ── 거래 부대비(§5 — bidsim 과 동일 상수) ────────────────────────────
  function stampTax(rules, price) {
    var bs = rules.costs.stamp_brackets;
    for (var i = 0; i < bs.length; i++) {
      if (bs[i][0] === null || price <= bs[i][0]) return bs[i][1];
    }
    return bs[bs.length - 1][1];
  }

  // feeKind: 'housing' | 'officetel' | 'nonhousing' (오피스텔은 면적으로 주거용 판별)
  function agentFee(rules, sellPrice, feeKind, areaM2) {
    if (sellPrice <= 0) return 0;
    var C = rules.costs;
    if (feeKind === 'housing') {
      var bs = C.agent_fee_housing;
      for (var i = 0; i < bs.length; i++) {
        if (bs[i][0] === null || sellPrice < bs[i][0]) {
          var fee = Math.round(sellPrice * bs[i][1]);
          return bs[i][2] === null ? fee : Math.min(fee, bs[i][2]);
        }
      }
    }
    if (feeKind === 'officetel' && areaM2 > 0 && areaM2 <= C.residential_officetel_max_m2) {
      return Math.round(sellPrice * C.agent_fee_residential_officetel);
    }
    return Math.round(sellPrice * C.agent_fee_nonhousing);
  }

  function longTermDeductionRate(rules, months) {
    var L = rules.costs.ltd;
    var years = Math.floor(months / 12);
    if (years < L.min_years) return 0;
    return Math.min(L.max_rate, years * L.per_year);
  }

  // ── 소득세 공통(누진 브래킷: 세액 = 과표×세율 − 누진공제) ─────────────
  function basicIncomeTax(rules, taxable) {
    if (taxable <= 0) return 0;
    var bs = rules.transfer_individual.basic_brackets;
    for (var i = 0; i < bs.length; i++) {
      if (bs[i].upto === null || taxable <= bs[i].upto) {
        return Math.max(0, Math.round(taxable * bs[i].rate - bs[i].deduction));
      }
    }
    return 0;
  }

  function surchargedTax(rules, taxable, surcharge) {
    if (taxable <= 0) return 0;
    var bs = rules.transfer_individual.basic_brackets;
    for (var i = 0; i < bs.length; i++) {
      if (bs[i].upto === null || taxable <= bs[i].upto) {
        return Math.max(0, Math.round(taxable * (bs[i].rate + surcharge) - bs[i].deduction));
      }
    }
    return 0;
  }

  // 법인세(한계 누적 방식 — 브래킷에 누진공제가 없어 구간별 합산)
  function corpIncomeTax(rules, taxable) {
    if (taxable <= 0) return 0;
    var bs = rules.corp.brackets_fy2026, tax = 0, prev = 0;
    for (var i = 0; i < bs.length; i++) {
      var top = bs[i].upto === null ? taxable : Math.min(taxable, bs[i].upto);
      if (top > prev) tax += (top - prev) * bs[i].rate;
      prev = bs[i].upto;
      if (bs[i].upto !== null && taxable <= bs[i].upto) break;
    }
    return Math.round(tax);
  }

  // ── 매도 세금(프로필 분기의 심장) ────────────────────────────────────
  // o: {profile, salePrice, holdMonths, housingForTransfer, adjusted, housesAfter,
  //     bid, acqTaxTotal, stamp, registryCost, agentFee, assumedAmount,
  //     evictCost, unpaidMgmt, repairCost, interest, applyCorpAuctionExclusion}
  function saleTax(rules, o) {
    var R = rules.transfer_individual;
    var notes = [], method, national;
    var stamp = o.stamp || 0, registry = o.registryCost || 0, fee = o.agentFee || 0;
    var repair = o.repairCost || 0;

    if (o.profile === 'corp') {
      // 법인 본세: 경비 폭넓게 인정(이자·명도비·중개보수 포함 — 손금).
      var profit = o.salePrice - (o.bid + o.acqTaxTotal + stamp + registry + o.assumedAmount
                                  + o.evictCost + o.unpaidMgmt + repair + o.interest + fee);
      var base = corpIncomeTax(rules, profit);
      // 추가과세(§55조의2) 과표는 본세와 별도: 양도가액 − **장부가액**(취득원가 자본화분 =
      // 낙찰가+취득세+인지세+등기+인수금). 이자(기간비용)·중개보수(양도비용)·명도비는 장부가액이
      // 아니므로 여기서 빼면 추가과세 과소(낙관) — 감사 확정 결함의 수정.
      var addBase = Math.max(0, o.salePrice - (o.bid + o.acqTaxTotal + stamp + registry + o.assumedAmount));
      var add = 0;
      if (o.housingForTransfer && addBase > 0) {
        if (o.applyCorpAuctionExclusion) {
          notes.push('경매취득 3년 내 양도 추가과세 제외 토글 적용 중 — 제3자 낙찰 법인 적용 여부는 유권해석 필요(기본은 미적용)');
        } else {
          add = Math.round(addBase * rules.corp.housing_addtax);
        }
      }
      national = Math.max(0, base + add);
      method = '법인세(10~25% 구간)' + (add ? ' + 주택 추가과세 20%p' : '');
    } else if (o.profile === 'dealer') {
      // 매매사업자: 종소세 후보는 경비 폭넓게(이자·명도 포함). 중과대상(조정지역 & 세대 2주택 이상)만
      // §64 비교과세, 그 외 주택 단기는 **기본세율 종합과세**(§64 가 단기세율 미인용 — 원문 검증 확정).
      var p = o.salePrice - (o.bid + o.acqTaxTotal + stamp + registry + o.assumedAmount
                             + o.evictCost + o.unpaidMgmt + repair + o.interest + fee);
      var cands = [basicIncomeTax(rules, p)];
      method = '종합소득세(기본세율)';
      if (o.housingForTransfer && o.adjusted && o.housesAfter >= 2) {
        // §64 비교 후보의 과표는 '주택등매매차익' = **양도소득 방식**(시행령 §122: 매매가액 −
        // §97 필요경비(취득가액·양도비) − 기본공제 250만). 이자·명도비 등 일반경비는 여기 불산입 —
        // 광의 경비 차익(p)으로 계산하면 세액 과소(낙관), 감사 확정 결함의 수정.
        var tbase = Math.max(0, o.salePrice
          - (o.bid + o.acqTaxTotal + stamp + registry + fee + o.assumedAmount)
          - R.basic_deduction_annual);
        var sur = o.housesAfter >= 3 ? R.multi_home_surcharge.homes_3_plus : R.multi_home_surcharge.homes_2;
        cands.push(surchargedTax(rules, tbase, sur));
        if (o.holdMonths < 24) cands.push(Math.round(tbase * (o.holdMonths < 12 ? R.short_term.housing.lt_1y : R.short_term.housing.lt_2y)));
        method = '비교과세(§64) — 종소세 vs 중과/단기 중 큰 세액';
        notes.push('조정대상지역 + 세대 ' + o.housesAfter + '주택 → 중과대상 주택으로 비교과세 진입');
      } else if (o.holdMonths < 24) {
        notes.push('중과대상 아닌 주택 단기 차익 → 기본세율 종합과세(개인 단기 ' + (o.holdMonths < 12 ? '70%' : '60%') + ' 미적용) — 매매사업자 핵심 이점');
      }
      notes.push('사업성(계속·반복성) 인정 전제 — 등록만으로는 부인 가능. 다른 종합소득 미반영.');
      national = Math.max(0, Math.max.apply(null, cands));
    } else {
      // 개인: bidsim.transfer_tax 와 동일 골격. 필요경비 = 취득세+인지세+등기부대비+매도중개보수
      // (+인수보증금 — 국심 2006서3481, bidsim 과의 유일한 의도적 차이). 명도비·이자 불산입(보수).
      var expenses = o.acqTaxTotal + stamp + registry + fee + o.assumedAmount;
      var gain = o.salePrice - o.bid - expenses;
      if (gain <= 0) {
        national = 0; method = '양도차익 없음';
      } else {
        var flat = null, label;
        if (o.holdMonths < 12) { flat = o.housingForTransfer ? R.short_term.housing.lt_1y : R.short_term.non_housing.lt_1y; label = '1년 미만 단기 ' + flat * 100 + '%'; }
        else if (o.holdMonths < 24) { flat = o.housingForTransfer ? R.short_term.housing.lt_2y : R.short_term.non_housing.lt_2y; label = '1~2년 단기 ' + flat * 100 + '%'; }
        else label = '기본세율(6~45%)';
        var surApplies = o.housingForTransfer && o.adjusted && o.housesAfter >= 2;
        // 장특공: 단기세율·중과 대상은 배제(원문 검증) — 3년 이상 기본세율 경로만.
        var ltdRate = (flat !== null || surApplies) ? 0 : longTermDeductionRate(rules, o.holdMonths);
        var income = gain - Math.round(gain * ltdRate);
        var base2 = Math.max(0, income - R.basic_deduction_annual);
        var cand = flat !== null ? Math.round(base2 * flat) : basicIncomeTax(rules, base2);
        method = label;
        if (surApplies) {
          var s2 = o.housesAfter >= 3 ? R.multi_home_surcharge.homes_3_plus : R.multi_home_surcharge.homes_2;
          var baseNold = Math.max(0, gain - R.basic_deduction_annual); // 중과는 장특공 배제
          var mtax = surchargedTax(rules, baseNold, s2);
          if (mtax > cand) { cand = mtax; method = '다주택 중과(기본+' + s2 * 100 + '%p, 2026-05-10 재개)' + (flat !== null ? ' — 단기세율과 경합해 큰 세액' : ''); }
        }
        if (o.housingForTransfer && o.housesAfter === 1 && o.holdMonths >= 24) {
          notes.push('1세대1주택 비과세(12억 이하·2년 보유, 조정지역 취득 시 2년 거주) 해당 가능 — 보수적으로 미적용 계산');
        }
        if (ltdRate === 0 && o.holdMonths < 36) notes.push('장기보유특별공제(3년 이상) 미해당 구간 — 미적용');
        national = cand;
      }
    }
    var local = Math.round(national * R.local_surtax);
    return { national: national, local: local, total: national + local, method: method, notes: notes };
  }

  // ── 재산세 약식(공시가 미보유 → 시세×0.7 근사, 가정 칩 필수) ─────────
  function propertyTaxApprox(rules, marketPrice) {
    var H = rules.holding;
    var official = marketPrice * H.approx_official_price_ratio.value;
    var base = official * H.fair_market_ratio;
    var bs = H.prop_tax_housing_brackets, main = 0;
    for (var i = 0; i < bs.length; i++) {
      if (bs[i].upto === null || base <= bs[i].upto) { main = Math.round(bs[i].base + (base - bs[i].over) * bs[i].rate); break; }
    }
    var city = Math.round(base * H.city_area_rate);
    var edu = Math.round(main * H.edu_tax_on_prop);
    return { total: main + city + edu, breakdown: { main: main, city: city, edu: edu },
             assumption: '공시가=시세×0.7 근사(공시가 미보유), 표준세율 기준' };
  }

  // ── 잔금 타임라인(감사 U-03 흡수) — 매각기일 기준 근사 ────────────────
  function timeline(saleDateStr) {
    var d0 = new Date(saleDateStr + 'T00:00:00');
    if (isNaN(d0)) return null;
    // toISOString 은 UTC 변환이라 KST 자정이 전날로 밀린다 — 로컬 필드로 직접 포맷.
    function fmt(d) {
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
    function plus(days) { var d = new Date(d0); d.setDate(d.getDate() + days); return fmt(d); }
    return {
      sale_date: saleDateStr,
      permit_date: plus(7),        // 매각허가결정(통상 +7일)
      confirm_date: plus(14),      // 허가 확정(+7일, 항고 없을 때)
      payment_deadline: plus(44),  // 잔금기한(확정 후 ~30일 — 법원 통지 기준, 근사)
      assumption: '허가 +7일·확정 +7일·잔금 ~30일 통상 일정 근사 — 법원 통지가 우선',
    };
  }

  // 소유 구간 [취득일, 취득일+holdMonths]가 6월 1일(재산세·종부세 과세기준일)을 몇 번 지나는가.
  // 18·30개월 보유는 6/1 을 2~3번 만날 수 있다 — 1회로 고정하면 다년 보유 세후익 과대(감사 확정 결함).
  function countJune1(acqDateStr, holdMonths) {
    var a = new Date(acqDateStr + 'T00:00:00');
    if (isNaN(a)) return 0;
    var b = new Date(a); b.setMonth(b.getMonth() + holdMonths);
    var n = 0;
    for (var y = a.getFullYear(); y <= b.getFullYear(); y++) {
      var j = new Date(y, 5, 1); // 6/1
      if (j > a && j <= b) n++;
    }
    return n;
  }

  function spansJune1(acqDateStr, holdMonths) { return countJune1(acqDateStr, holdMonths) > 0; }

  // ── 프로필 1개 전체 현금흐름 ─────────────────────────────────────────
  function simulateProfile(rules, facts, inputs, profile, holdMonths) {
    var housesAfter = (inputs.housesOwned || 0) + 1;
    var adjusted = facts.regulated && facts.regulated.adjusted === true;
    var acq = acquisitionTax(rules, {
      price: inputs.bidPrice, isHousing: facts.isHousingAcq, profile: profile,
      adjusted: adjusted, housesAfter: housesAfter, areaM2: facts.areaM2 || 0,
    });
    var stamp = stampTax(rules, inputs.bidPrice);
    var registry = inputs.registryCost != null ? inputs.registryCost : rules.costs.registry_default;
    var repair = inputs.repairCost || 0;
    var totalIn = inputs.bidPrice + acq.total + stamp + registry + inputs.evictCost
      + inputs.unpaidMgmt + repair + (facts.assumedAmount || 0);
    var loan = Math.round(inputs.bidPrice * inputs.loanRatio);
    var cash = totalIn - loan;
    var interest = Math.round(loan * inputs.interestRate * holdMonths / 12);
    var fee = agentFee(rules, inputs.salePrice, facts.feeKind || 'housing', facts.areaM2 || 0);

    var tl = facts.saleDate ? timeline(facts.saleDate) : null;
    var propTax = 0, propNote = null;
    if (tl && facts.isHousingAcq) {
      var nJune = countJune1(tl.payment_deadline, holdMonths);
      if (nJune > 0) {
        var pt = propertyTaxApprox(rules, inputs.salePrice);
        propTax = pt.total * nJune;   // 연도별 발생 — 다년 보유는 곱한다
        propNote = '보유 구간이 6/1(과세기준일)을 ' + nJune + '회 지남 — 재산세 약식 ×' + nJune + ' (' + pt.assumption + ')';
      }
    }

    var st = saleTax(rules, {
      profile: profile, salePrice: inputs.salePrice, holdMonths: holdMonths,
      housingForTransfer: facts.housingForTransfer, adjusted: adjusted, housesAfter: housesAfter,
      bid: inputs.bidPrice, acqTaxTotal: acq.total, stamp: stamp, registryCost: registry,
      agentFee: fee, assumedAmount: facts.assumedAmount || 0,
      evictCost: inputs.evictCost, unpaidMgmt: inputs.unpaidMgmt, repairCost: repair,
      interest: interest, applyCorpAuctionExclusion: !!inputs.applyCorpAuctionExclusion,
    });

    var net = inputs.salePrice - totalIn - interest - propTax - fee - st.total;
    return {
      profile: profile, holdMonths: holdMonths,
      acquisition: acq, stamp: stamp, registryCost: registry, agentFee: fee,
      totalIn: totalIn, loan: loan, cash: cash,
      interest: interest, propTax: propTax, propNote: propNote,
      saleTax: st, netProfit: net,
      roi: cash > 0 ? net / cash : null,
      roiMonthly: cash > 0 && holdMonths > 0 ? net / cash / holdMonths : null,
      timeline: tl,
    };
  }

  // 손익분기 매도가 역산(이분탐색 — 순익은 매도가에 단조증가).
  // ⚠ 근을 반드시 괄호로 감싼다: 상한에서 순익이 음수인 채 탐색하면 상한값(임의 캡)이 그대로
  // 반환돼 손익분기가 1억+ 낮게 표시됐다(인수액 큰 물건 + 매도가 0 초기값 — 감사 확정 결함).
  function breakeven(rules, facts, inputs, profile, holdMonths) {
    function net(x) {
      return simulateProfile(rules, facts, Object.assign({}, inputs, { salePrice: x }), profile, holdMonths).netProfit;
    }
    var lo = 0, hi = Math.max(inputs.bidPrice * 4, inputs.salePrice * 3, 100000000);
    var guard = 0;
    while (guard++ < 50 && net(hi) <= 0) hi *= 2;   // 상한 배증 확장으로 근 포섭
    if (net(hi) <= 0) return null;                   // 이 조건에선 손익분기 자체가 없음(정직 표기)
    for (var i = 0; i < 60; i++) {
      var mid = (lo + hi) / 2;
      if (net(mid) > 0) hi = mid; else lo = mid;
    }
    return Math.round(hi);
  }

  // ── 최상위: 3프로필 × 보유기간 밴드 매트릭스 + 경고 ──────────────────
  function simulate(rules, facts, inputs) {
    var profiles = ['individual', 'dealer', 'corp'];
    var matrix = {};
    profiles.forEach(function (p) {
      matrix[p] = MONTH_BANDS.map(function (m) { return simulateProfile(rules, facts, inputs, p, m); });
    });
    var current = {};
    profiles.forEach(function (p) {
      current[p] = simulateProfile(rules, facts, inputs, p, inputs.holdMonths);
      current[p].breakeven = breakeven(rules, facts, inputs, p, inputs.holdMonths);
    });
    var warnings = [];
    if (facts.rightsUnverified) warnings.push('권리분석 미확인 물건 — 인수금·명도비가 기본 가정(0·최소)으로 계산됨. 명세서·등기부 확인 전에는 이 숫자를 하한으로 믿지 말 것');
    if (facts.burdenUnknown) warnings.push('인수금액 미상 — 아래 숫자는 하한이 아님(명세서 인수 명시·금액 불명)');
    if (facts.regulated && facts.regulated.adjusted === 'check') warnings.push('규제지역 판정 불가(화성시 비동탄 등) — 비규제 가정으로 계산, 확인 필요');
    if (facts.regulated && facts.regulated.landPermit) warnings.push('토지거래허가구역 — 경매 낙찰은 허가 불요(법 §14②2호)·실거주 의무도 미적용, 단 매도 시 매수인은 허가 대상');
    if (facts.isHousingAcq === false && facts.housingForTransfer) warnings.push('오피스텔: 취득세는 4.6%(비주택), 양도세는 주거용 사용 시 주택 취급 가정 — 실사용에 따라 달라짐');
    // 부가세 리스크: 85㎡ 초과 주택뿐 아니라 상가·업무용 오피스텔·토지는 면적 무관(감사 확정 — 종전엔 85 초과만 경고)
    if (!facts.isHousingAcq || (facts.areaM2 || 0) > 85) warnings.push('매매사업자·법인 매도 시 건물분 부가세 10% 발생 가능(85㎡ 초과 주택·상가·업무용 오피스텔 — 미계산)');
    warnings.push('종부세 미계산(법인·매매사업자는 6/1 보유 시 별도 유의) · DSR/개인 소득 미반영 · 세무 조언 아님(기준일 ' + rules._meta.basis_date + ')');
    return { matrix: matrix, current: current, warnings: warnings, bands: MONTH_BANDS };
  }

  var api = {
    acquisitionTax: acquisitionTax, stampTax: stampTax, agentFee: agentFee,
    longTermDeductionRate: longTermDeductionRate,
    basicIncomeTax: basicIncomeTax, corpIncomeTax: corpIncomeTax,
    saleTax: saleTax, propertyTaxApprox: propertyTaxApprox, timeline: timeline,
    spansJune1: spansJune1, countJune1: countJune1,
    simulateProfile: simulateProfile, breakeven: breakeven, simulate: simulate,
    MONTH_BANDS: MONTH_BANDS,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DealSim = api;
})(typeof self !== 'undefined' ? self : globalThis);
