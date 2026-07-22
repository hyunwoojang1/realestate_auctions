"""courtauction(대법원 법원경매) 물건검색 크롤러 — 정중·안전 우선.

엔드포인트(실측): POST https://www.courtauction.go.kr/pgj/pgjsearch/searchControllerMain.on
  body = {"dma_pageInfo":{...}, "dma_srchGdsDtlSrchInfo":{...}} (JSON)
  응답 = {"status":200,"data":{"dma_pageInfo":{...,"totalCnt"},"dlt_srchResult":[...]}}
  전제: GET /pgj/index.on 세션쿠키 + 브라우저 헤더 + Referer.

밴 회피 설계(리서치+적대적검토 반영):
  1) 요청 총량 최소화 — 작동하는 서버필터(지역/감정가/최저가율/면적/유찰)로 후보만 받음.
  2) concurrency=1, 요청 간 3~8초 랜덤 지터, 토큰버킷/일일 상한.
  3) 세션쿠키 재사용 + 정확한 헤더 + Referer/Origin. IP 변동 시 세션 재취득(쿠키에 IP 박힘).
  4) 지수 백오프(429/5xx). 403/차단 의심 → 즉시 중단(우회 금지).
  5) 콘텐츠 회로차단기 — 200인데 스키마 깨짐/HTML/리다이렉트 = 조용한 차단 감지 → 중단.
  6) 카나리 요청(반드시 결과 나오는 쿼리)로 정상 응답형태 사전 확인.
  7) kill-switch 파일 존재 시 즉시 종료.

합법 전제: 공공누리 제4유형(비영리·개인용). 개인정보 필드 미저장(courtauction_fields가 처리).
서버 부하 미발생 수준 저빈도. 차단되면 우회하지 말고 중단 후 합법대안(CODEF 등) 검토.
"""
from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from .courtauction_fields import (
    SRCH_COND_REAL_ESTATE,
    CourtAuctionRecord,
    parse_row,
)

logger = logging.getLogger(__name__)

_RETRY_AFTER_MIN = 60.0   # 429 시 최소 대기(서버가 더 길게 요청하면 그 값 사용)

BASE = "https://www.courtauction.go.kr"
INDEX_URL = f"{BASE}/pgj/index.on"
SEARCH_URL = f"{BASE}/pgj/pgjsearch/searchControllerMain.on"
# 물건상세(사건 단위) — 매각물건명세서 요지(인수권리·최선순위)·기일내역·청구금액 등.
# 실측(2026-07-10, Playwright XHR 캡처): 미니멀 페이로드(csNo+cortOfcCd+dspslGdsSeq+pgmId)로 동작.
DETAIL_URL = f"{BASE}/pgj/pgj15B/selectAuctnCsSrchRslt.on"
# 현황조사서(부동산현황조사) — 임차인 전입일·확정일자·점유관계. 매각물건명세서 요지엔 없는
# '임차인 전입일'의 유일한 구조화 원천(2026-07-22 정찰, docs/courtauction_recon.md 3차).
# ★제약: 같은 세션에서 DETAIL_URL(case_detail)을 선행해야 응답이 온다 — 선행 없이 호출하면
# 200이지만 {ipcheck:false} 빈 응답(서버가 사건 컨텍스트를 세션에서 확인). 타 세션 라이브검증 완료.
CURST_URL = f"{BASE}/pgj/pgj15B/selectCurstExmndc.on"
# (D1) 당일 요청 예산 공유 파일 — 프로덕션 크롤이 이 경로로 budget_file 을 켜서 목록·권리 크롤이
# 같은 일일 상한을 공유한다(프로세스 재시작·병렬 실행이 밴 상한을 우회하지 못하게).
BUDGET_FILE = ".courtauction_budget.json"

# 브라우저 위장 헤더(실측상 필수 6종 + 보강). requests 기본 UA는 즉시 봇 차단됨.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_BASE_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}
_POST_HEADERS = {
    **_BASE_HEADERS,
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Referer": INDEX_URL,
    "Origin": BASE,
    "X-Requested-With": "XMLHttpRequest",
}

PAGE_SIZE = 40            # 실측 상한(200은 HTTP400). 초과 금지.


class CourtAuctionError(RuntimeError):
    """일반 호출 실패(네트워크·파싱)."""


class CourtAuctionBlocked(CourtAuctionError):
    """차단/조용한차단 감지 — 즉시 중단해야 하는 상황(403, 스키마 붕괴, kill-switch)."""


# ---------------------------------------------------------------------------
# 검색 필터 — '작동 확인된' 서버사이드 필터만 노출(절대 최저가는 무시되므로 제외)
# ---------------------------------------------------------------------------
@dataclass
class SearchFilter:
    """물건 검색조건. 빈 값은 미적용. (실측: 지역/감정가/최저가율/면적/유찰만 서버에서 동작)"""
    sido_cd: str = ""              # rprsAdongSdCd  예) "11"=서울 ("" = 전국)
    sigu_cd: str = ""              # rprsAdongSggCd 시군구(선택)
    appraisal_min: int = 0         # aeeEvlAmtMin (원)
    appraisal_max: int = 0         # aeeEvlAmtMax (원) — 가용현금 프록시의 핵심 레버
    price_rate_min: int = 0        # lwsDspslPrcRateMin (%) 최저가율 하한
    price_rate_max: int = 0        # lwsDspslPrcRateMax (%)
    area_min: float = 0.0          # objctArDtsMin (㎡)
    area_max: float = 0.0          # objctArDtsMax (㎡)
    fail_count_min: int = 0        # flbdNcntMin 유찰 하한
    fail_count_max: int = 0        # flbdNcntMax
    usage_lcls: str = ""           # lclDspslGdsLstUsgCd 용도대분류
    usage_mcls: str = ""           # mclDspslGdsLstUsgCd
    usage_scls: str = ""           # sclDspslGdsLstUsgCd

    # 검색대상 키 전체(서버가 빈 값도 요구) — searchControllerMain 페이로드 골격
    _ALL_KEYS = (
        "rletDspslSpcCondCd", "bidDvsCd", "mvprpRletDvsCd", "cortAuctnSrchCondCd",
        "rprsAdongSdCd", "rprsAdongSggCd", "rprsAdongEmdCd", "rdnmSdCd", "rdnmSggCd", "rdnmNo",
        "mvprpDspslPlcAdongSdCd", "mvprpDspslPlcAdongSggCd", "mvprpDspslPlcAdongEmdCd",
        "rdDspslPlcAdongSdCd", "rdDspslPlcAdongSggCd", "rdDspslPlcAdongEmdCd",
        "cortOfcCd", "jdbnCd", "execrOfcDvsCd",
        "lclDspslGdsLstUsgCd", "mclDspslGdsLstUsgCd", "sclDspslGdsLstUsgCd", "cortAuctnMbrsId",
        "aeeEvlAmtMin", "aeeEvlAmtMax", "rletLwsDspslPrcMin", "rletLwsDspslPrcMax",
        "mvprpLwsDspslPrcMin", "mvprpLwsDspslPrcMax", "lwsDspslPrcRateMin", "lwsDspslPrcRateMax",
        "flbdNcntMin", "flbdNcntMax", "objctArDtsMin", "objctArDtsMax",
        "mvprpArtclKndCd", "mvprpArtclNm", "mvprpAtchmPlcTypCd", "notifyLoc",
        "lafjOrderBy", "pgmId", "csNo", "cortStDvs", "statNum", "bidBgngYmd", "bidEndYmd",
    )

    def to_payload(self) -> dict:
        """dma_srchGdsDtlSrchInfo dict 생성(전 키 빈값 + 설정값 덮어쓰기)."""
        d = {k: "" for k in self._ALL_KEYS}
        d["cortAuctnSrchCondCd"] = SRCH_COND_REAL_ESTATE  # 부동산
        d["notifyLoc"] = "Y"
        d["pgmId"] = "PGJ151M01"
        if self.sido_cd:
            d["rprsAdongSdCd"] = self.sido_cd
        if self.sigu_cd:
            d["rprsAdongSggCd"] = self.sigu_cd
        if self.appraisal_min:
            d["aeeEvlAmtMin"] = str(self.appraisal_min)
        if self.appraisal_max:
            d["aeeEvlAmtMax"] = str(self.appraisal_max)
        if self.price_rate_min:
            d["lwsDspslPrcRateMin"] = str(self.price_rate_min)
        if self.price_rate_max:
            d["lwsDspslPrcRateMax"] = str(self.price_rate_max)
        if self.area_min:
            d["objctArDtsMin"] = str(self.area_min)
        if self.area_max:
            d["objctArDtsMax"] = str(self.area_max)
        if self.fail_count_min:
            d["flbdNcntMin"] = str(self.fail_count_min)
        if self.fail_count_max:
            d["flbdNcntMax"] = str(self.fail_count_max)
        if self.usage_lcls:
            d["lclDspslGdsLstUsgCd"] = self.usage_lcls
        if self.usage_mcls:
            d["mclDspslGdsLstUsgCd"] = self.usage_mcls
        if self.usage_scls:
            d["sclDspslGdsLstUsgCd"] = self.usage_scls
        return d


def _page_info(page_no: int, total_yn: str = "Y") -> dict:
    return {"pageNo": str(page_no), "pageSize": str(PAGE_SIZE), "bfPageNo": "",
            "startRowNo": "", "totalCnt": "", "totalYn": total_yn}


# ---------------------------------------------------------------------------
# 클라이언트
# ---------------------------------------------------------------------------
@dataclass
class CourtAuctionClient:
    """저빈도·안전 크롤러. with 블록 또는 직접 사용. 라이브 호출은 외부망 필요."""
    min_interval: float = 3.0          # 요청 간 최소 지연(초)
    max_interval: float = 8.0          # 최대 지연 → 그 사이 랜덤(고정간격=봇)
    daily_cap: int = 500               # 1일 총 요청 상한(서킷)
    max_retries: int = 4               # 429/5xx 재시도 횟수
    backoff_base: float = 2.0          # 백오프 기준(2→4→8…)
    backoff_cap: float = 120.0
    timeout: int = 30
    stop_file: str | None = "COURTAUCTION_STOP"   # 존재하면 즉시 중단(kill-switch). 전용 파일명(루프의 AGENT_STOP과 분리)
    # (D1 2026-07-22 QA CRITICAL) 일일 요청수를 날짜별로 디스크에 영속 — 종전엔 _request_count가
    # 생성자마다 0으로 리셋돼 daily_cap이 프로세스마다 새로 시작, 실제 하루 6119콜(상한 12배)이
    # 단일 IP로 나갔다. 목록크롤·권리크롤·재시작이 같은 파일로 **당일 예산을 공유**한다(정부사이트 밴 방지).
    # 기본 None=비영속(테스트·단발 조회는 상태 공유 안 함). 프로덕션 크롤(pipeline·crawl_rights)이
    # BUDGET_FILE 을 명시로 켠다. 켜야 프로세스 간 당일 예산 공유가 작동.
    budget_file: str | None = None
    session: object = None             # requests.Session (None이면 lazy 생성)
    _request_count: int = field(default=0, init=False)
    _client_ip: str = field(default="", init=False)
    _last_request_ts: float = field(default=0.0, init=False)

    def __post_init__(self):
        # 당일 누적 요청수를 이어받아 시작(프로세스 간 공유). 파일 없음/타일자면 0.
        self._request_count = self._load_budget()

    # --- 일일 요청 예산(영속) ---
    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _load_budget(self) -> int:
        if not self.budget_file:
            return 0
        try:
            d = json.loads(Path(self.budget_file).read_text(encoding="utf-8"))
            return int(d.get("count", 0)) if d.get("date") == self._today() else 0
        except Exception:  # noqa: BLE001 — 파일 없음/손상은 0에서 시작(영속 실패가 크롤을 막지 않음)
            return 0

    def _save_budget(self) -> None:
        if not self.budget_file:
            return
        try:
            Path(self.budget_file).write_text(
                json.dumps({"date": self._today(), "count": self._request_count}), encoding="utf-8")
        except Exception:  # noqa: BLE001 — 영속 실패는 인스턴스 카운트로 폴백(크롤 계속)
            pass

    # --- 세션/IP ---
    def _ensure_session(self):
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update(_BASE_HEADERS)
        return self.session

    def _warm_session(self) -> None:
        """GET /pgj/index.on — 세션쿠키 발급. wcCookieV2에서 클라이언트 IP 기록.

        주의: 가정용 동적 IP가 search() 진행 중 바뀌면 쿠키(IP 박힘)가 무효화될 수 있다.
        그 경우 다음 요청이 403/리다이렉트/스키마붕괴로 드러나 회로차단기가 중단시킨다
        (스트림 도중 자동 재워밍은 v1 미구현 — 의도적 한계).
        """
        s = self._ensure_session()
        r = s.get(INDEX_URL, timeout=self.timeout)
        if r.status_code != 200:
            raise CourtAuctionError(f"세션 워밍 실패 HTTP {r.status_code}")
        ip = self._extract_ip(r)
        if ip:
            self._client_ip = ip
        # IP는 운용자 자신의 주소 — DEBUG로만, 앞부분만 남겨 로그 유출 최소화.
        logger.debug("세션 워밍 완료 (client_ip=%s)", (self._client_ip[:7] + "…") if self._client_ip else "?")

    @staticmethod
    def _extract_ip(resp) -> str:
        """wcCookieV2('59.6.135.109_T_..._WC')에서 IP 추출. 실패는 무음이 아니라 로그."""
        try:
            cookie = resp.headers.get("Set-Cookie", "") or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("Set-Cookie 헤더 읽기 실패(IP 추출 불가): %s", e)
            return ""
        for part in cookie.split(","):
            if "wcCookieV2=" in part:
                val = part.split("wcCookieV2=", 1)[1].split(";", 1)[0]
                ip = val.split("_", 1)[0]
                if not ip:
                    logger.warning("wcCookieV2 파싱했으나 IP 부분이 빔")
                return ip
        logger.debug("Set-Cookie에 wcCookieV2 없음 — IP 미기록 응답")
        return ""

    # --- 안전장치 ---
    def _check_kill_switch(self) -> None:
        if self.stop_file and Path(self.stop_file).exists():
            raise CourtAuctionBlocked(f"kill-switch '{self.stop_file}' 존재 — 즉시 중단")

    def _throttle(self) -> None:
        """요청 간 랜덤 지터 지연(고정 간격=봇 신호이므로 매번 난수). 상한검사는 _post가 담당."""
        if self._last_request_ts:
            elapsed = time.monotonic() - self._last_request_ts
            wait = random.uniform(self.min_interval, self.max_interval) - elapsed
            if wait > 0:
                time.sleep(wait)

    def _post(self, body: dict, url: str = SEARCH_URL, validator=None) -> dict:
        """POST 1회(재시도 포함) — 스로틀·백오프·차단감지 내장. 정상 JSON dict 반환.

        url/validator 파라미터로 검색 외 엔드포인트(물건상세 등)도 같은 안전장치를 경유한다.
        _request_count는 '실제 서버로 보낸 요청 수'(재시도 포함)를 센다 — WAF가 보는 트래픽과 일치.
        직렬화는 루프 밖에서 1회만(직렬화/프로그래밍 오류는 재시도 없이 즉시 전파).
        """
        self._check_kill_switch()
        self._throttle()
        s = self._ensure_session()
        payload = json.dumps(body)   # 직렬화 오류는 여기서 즉시 전파(재시도 대상 아님)
        attempt = 0
        while True:
            attempt += 1
            if self._request_count >= self.daily_cap:
                raise CourtAuctionBlocked(f"요청 상한({self.daily_cap}, 당일 누적) 도달 — 중단")
            self._last_request_ts = time.monotonic()
            self._request_count += 1
            self._save_budget()   # (D1) 요청마다 당일 누적을 영속 — 재시작·타 프로세스가 이어받음
            try:
                resp = s.post(url, headers=_POST_HEADERS, data=payload,
                              timeout=self.timeout, allow_redirects=False)
            except requests.exceptions.RequestException as e:
                # 네트워크/HTTP 라이브러리 오류만 재시도. 그 외(프로그래밍 오류)는 전파됨.
                if attempt > self.max_retries:
                    raise CourtAuctionError(f"네트워크 오류 {self.max_retries}회 실패: {e}") from e
                self._sleep_backoff(attempt, f"네트워크 오류: {type(e).__name__}")
                continue

            status = resp.status_code
            # 403/리다이렉트 = 차단/세션이상 → 우회 금지, 즉시 중단
            if status == 403:
                raise CourtAuctionBlocked("HTTP 403 — 차단 의심. 즉시 중단(우회 금지).")
            if status in (301, 302, 303, 307, 308):
                raise CourtAuctionBlocked(f"HTTP {status} 리다이렉트(→index) — 세션무효/차단 의심.")
            if status == 429 or 500 <= status < 600:
                if attempt > self.max_retries:
                    raise CourtAuctionBlocked(f"HTTP {status} {self.max_retries}회 — 중단.")
                self._sleep_backoff(attempt, f"HTTP {status}", retry_after=resp.headers.get("Retry-After"))
                continue
            if status != 200:
                # 응답 본문은 민감정보(쿠키·내부오류·PII) 포함 가능 → 예외엔 안 싣고 DEBUG 로그만.
                logger.debug("예상치 못한 HTTP %s 응답본문: %.200s", status, resp.text)
                raise CourtAuctionError(f"예상치 못한 HTTP {status} (본문은 DEBUG 로그 참조)")
            return (validator or self._validate_payload)(resp)

    def _sleep_backoff(self, attempt: int, why: str, retry_after: str | None = None) -> None:
        delay = self._parse_retry_after(retry_after)
        if delay is None:
            delay = min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))
        delay += random.uniform(0, 2)
        logger.warning("%s — %.1fs 백오프 후 재시도(%d/%d)", why, delay, attempt, self.max_retries)
        time.sleep(delay)

    @staticmethod
    def _parse_retry_after(retry_after: str | None) -> float | None:
        """Retry-After(초 또는 HTTP-date) → 대기초(최소 _RETRY_AFTER_MIN). 없으면 None."""
        if not retry_after:
            return None
        ra = str(retry_after).strip()
        if ra.isdigit():
            return max(_RETRY_AFTER_MIN, float(ra))
        try:  # HTTP-date 형식
            when = parsedate_to_datetime(ra)
            return max(_RETRY_AFTER_MIN, when.timestamp() - time.time())
        except (TypeError, ValueError):
            return None

    def _validate_payload(self, resp) -> dict:
        """콘텐츠 회로차단기: 200이라도 스키마 깨지면 '조용한 차단'으로 간주."""
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise CourtAuctionBlocked(f"200인데 비-JSON({ctype}) — 위장차단 의심. 중단.")
        try:
            j = resp.json()
        except Exception as e:  # noqa: BLE001
            raise CourtAuctionBlocked(f"200인데 JSON 파싱불가 — 위장차단 의심: {e}") from e
        if not isinstance(j, dict) or "data" not in j or not isinstance(j.get("data"), dict):
            errs = j.get("errors") if isinstance(j, dict) else None
            raise CourtAuctionBlocked(f"응답 스키마 이상(data 없음). errors={errs}")
        data = j["data"]
        if "dlt_srchResult" not in data:
            raise CourtAuctionBlocked("응답에 dlt_srchResult 없음 — 스키마 붕괴/차단 의심.")
        if "dma_pageInfo" not in data:
            raise CourtAuctionBlocked("응답에 dma_pageInfo 없음 — 스키마 붕괴/차단 의심.")
        return j

    def _validate_detail(self, resp) -> dict:
        """물건상세 응답 회로차단기 — dma_result 스키마 확인(검색과 응답 형태가 다름)."""
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise CourtAuctionBlocked(f"상세 200인데 비-JSON({ctype}) — 위장차단 의심. 중단.")
        try:
            j = resp.json()
        except Exception as e:  # noqa: BLE001
            raise CourtAuctionBlocked(f"상세 200인데 JSON 파싱불가 — 위장차단 의심: {e}") from e
        data = j.get("data") if isinstance(j, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("dma_result"), dict):
            errs = j.get("errors") if isinstance(j, dict) else None
            raise CourtAuctionBlocked(f"상세 응답 스키마 이상(dma_result 없음). errors={errs}")
        return j

    # --- 공개 API ---
    def case_detail(self, cort_ofc_cd: str, cs_no: str, gds_seq: str = "1",
                    warm: bool = True) -> dict:
        """물건상세(사건 단위) 조회 → dma_result dict.

        포함(실측): csBaseInfo(청구금액·경매계), dspslGdsDxdyInfo(매각물건명세서 요지 —
        ndstrcRghCtt 인수권리 / tprtyRnkHypthcStngDts 최선순위 설정 / sprfcExstcDts 유치권),
        gdsDspslDxdyLst(기일 역사), dstrtDemnInfo(배당요구종기).
        cs_no 는 사용자 포맷("2025타경669")·내부 포맷 둘 다 서버가 수용(실측은 사용자 포맷).
        """
        if warm and not self._client_ip:
            self._warm_session()
        body = {"dma_srchGdsDtlSrch": {"csNo": cs_no, "cortOfcCd": cort_ofc_cd,
                                       "dspslGdsSeq": str(gds_seq), "pgmId": "PGJ151F01"}}
        j = self._post(body, url=DETAIL_URL, validator=self._validate_detail)
        return j["data"]["dma_result"]

    def case_curst_survey(self, cort_ofc_cd: str, cs_no: str) -> dict:
        """현황조사서 조회 → 임차인 전입일·점유관계 원재료 dict.

        ★반드시 같은 세션에서 case_detail(같은 사건)을 **선행 호출한 뒤** 부를 것 —
        선행 없이 부르면 서버가 {ipcheck:false} 빈 응답을 준다(세션 사건 컨텍스트 필요).
        crawl_rights 는 물건당 case_detail 직후 이 메서드를 호출하므로 자연히 충족된다.

        반환: {ipcheck, dlt_ordTsLserLtn(임차인 리스트), dlt_curstExmnDpcnMrg(점유관계),
              dma_curstExmnMngInf(조사서 관리정보), ...}. ipcheck=false거나 리스트 없음이면
              '임차인 없음/미상'으로 처리(예외 아님). PII(성명·주민번호)는 파서에서 제거한다.

        스로틀·kill-switch·일일상한·백오프는 _post 가 담당(case_detail 과 동일 안전장치).
        """
        body = {"dma_srchCurstExmn": {"cortOfcCd": cort_ofc_cd, "csNo": cs_no,
                                      "auctnInfOriginDvsCd": "2"}}  # 2=현황조사서 고정
        # raw validator — 기본 검증기(_validate_payload)는 dlt_srchResult 를 기대해 이 응답을
        # 오탐 차단한다. Content-Type(JSON)만 확인하고 data 를 그대로 반환.
        j = self._post(body, url=CURST_URL, validator=self._validate_detail_lenient)
        return j.get("data") or {}

    @staticmethod
    def _validate_detail_lenient(resp) -> dict:
        """현황조사서용 관대한 검증 — JSON 이기만 하면 통과(빈 임차인표도 정상 응답).

        위장차단(HTML/비-JSON) 은 여전히 차단으로 판정하되, dma_result·특정 키 유무는
        요구하지 않는다(임차인 없는 물건은 빈 리스트로 정상 반환되므로)."""
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise CourtAuctionBlocked(f"현황조사서 200인데 비-JSON({ctype}) — 위장차단 의심.")
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise CourtAuctionBlocked(f"현황조사서 200인데 JSON 파싱불가 — 위장차단 의심: {e}") from e

    def canary(self) -> int:
        """반드시 결과가 나오는 알려진 쿼리(서울 부동산 1페이지)로 정상성 확인. totalCnt 반환."""
        self._warm_session()
        body = {"dma_pageInfo": _page_info(1), "dma_srchGdsDtlSrchInfo": SearchFilter(sido_cd="11").to_payload()}
        data = self._post(body)["data"]
        total = int(data["dma_pageInfo"].get("totalCnt") or 0)
        rows = len(data.get("dlt_srchResult") or [])
        if rows == 0 or total == 0:
            raise CourtAuctionBlocked("카나리 0건 — 정상 응답형태 아님(조용한 차단 의심). 작업 중단.")
        logger.info("카나리 OK (서울 totalCnt=%d, rows=%d)", total, rows)
        return total

    def search(self, flt: SearchFilter, max_pages: int = 25,
               warm: bool = True) -> Iterator[CourtAuctionRecord]:
        """검색조건으로 매물을 페이지네이션하며 yield. 개인정보는 raw에서 제거됨.

        max_pages로 안전 상한(기본 25p=1000행). totalCnt 도달 시 조기 종료.
        """
        if warm and not self._client_ip:
            self._warm_session()
        payload = flt.to_payload()
        first = self._post({"dma_pageInfo": _page_info(1), "dma_srchGdsDtlSrchInfo": payload})["data"]
        total = int(first["dma_pageInfo"].get("totalCnt") or 0)
        yielded = 0
        for row in (first.get("dlt_srchResult") or []):
            yielded += 1
            yield parse_row(row)
        if total == 0:
            logger.info("검색 결과 0건 (sido=%s appMax=%s)", flt.sido_cd, flt.appraisal_max)
            return
        if yielded >= total:        # 1페이지로 끝난 경우 추가 요청 안 함
            logger.info("검색 완료 %d행 수집 (총 %d, 1페이지)", yielded, total)
            return
        last_page = min(max_pages, -(-total // PAGE_SIZE))  # ceil
        for page in range(2, last_page + 1):
            data = self._post({"dma_pageInfo": _page_info(page, total_yn="N"),
                               "dma_srchGdsDtlSrchInfo": payload})["data"]
            rows = data.get("dlt_srchResult") or []
            if not rows:
                # 잔여가 있는데 빈 페이지 = 서버 일시장애/조용한 차단 의심 → 누락을 ERROR로 드러냄
                remaining = total - yielded
                if remaining > 0:
                    logger.error("page %d 0행 — 조기종료, 잔여 %d건 누락(수집 %d/총 %d). "
                                 "서버 일시장애/조용한차단 의심.", page, remaining, yielded, total)
                else:
                    logger.warning("page %d 0행 — 조기종료(잔여 없음 추정)", page)
                break
            for row in rows:
                yielded += 1
                yield parse_row(row)
            if yielded >= total:     # 목표 도달 → 불필요 요청 방지
                break
        full_pages = -(-total // PAGE_SIZE)
        if last_page < full_pages:
            # (재검증 감사 2026-07-11 idx21 CRITICAL) max_pages 잘림은 '의도적'이어도 침묵하면
            # 안 된다 — 기본값 10p=400행에서 부산 등 대형 시도의 유효매물 수백 건이 매일 조용히
            # 누락됐다. WARNING 으로 승격해 수집 배치 로그에서 즉시 드러나게 한다.
            logger.warning("⚠ 검색 잘림: %d행만 수집 (max_pages=%d 제한, 총 %d건 중 %d건 누락 "
                           "— --max-pages 상향 필요)", yielded, max_pages, total, total - yielded)
        elif yielded < total * 0.9:
            # 전 페이지를 돌았는데도 10%+ 부족 = 진짜 누락(서버이상/조용한차단) → 경고
            logger.warning("검색 종료 %d행 — 전 페이지 순회했으나 totalCnt %d 대비 누락률 %.0f%%",
                           yielded, total, (1 - yielded / total) * 100)
        else:
            logger.info("검색 완료 %d행 수집 (총 %d, %d페이지)", yielded, total, last_page)

    def affordable_search(self, cash_won: int, appraisal_buffer: float = 3.0,
                          extra: SearchFilter | None = None,
                          max_pages: int = 25, warm: bool = True) -> list[CourtAuctionRecord]:
        """'내 가용현금으로 살 수 있는' 매물.

        절대 최저가 서버필터가 막혀 있으므로(실측): 서버에선 감정가Max로 볼륨만 줄이고
        (감정가 = cash×buffer까지 — 다회유찰로 싸진 고감정가 매물 누락 방지),
        '최저가 ≤ 가용현금' 정밀 필터는 로컬에서 수행한다(적대적검토 반영).
        """
        # 호출자의 SearchFilter를 변이하지 않도록 복사(불변성).
        flt = replace(extra if extra is not None else SearchFilter(),
                      appraisal_max=int(cash_won * appraisal_buffer))
        out: list[CourtAuctionRecord] = []
        for rec in self.search(flt, max_pages=max_pages, warm=warm):
            if 0 < rec.min_bid_price <= cash_won:
                out.append(rec)
        logger.info("affordable: 현금 %d원 → %d건(감정가버퍼 ×%.1f)", cash_won, len(out), appraisal_buffer)
        return out
