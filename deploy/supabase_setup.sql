-- auction-arbitrage → Supabase(econ 프로젝트 trajmfklbyarbkiljogj) 클라우드 이관 스키마
-- 실행: Supabase Dashboard → SQL Editor → 아래 전체 붙여넣고 Run.
-- 안전: econ 기존 public.* 테이블을 건드리지 않음(신규 auction_ prefix 테이블만 생성).
--       raw_listings는 로컬 SQLite에 유지(용량 절약), 채점결과(scored_listings)만 클라우드.
-- 접근: 서버 전용 service key(SUPABASE_SECRET_KEY)로만 읽고 씀 → RLS on + 정책 없음 = anon 차단.

create table if not exists public.auction_scored_listings (
    case_no                text    not null,
    apt_name               text,
    address                text,
    property_type          text,
    area_m2                double precision,
    appraisal_price        bigint,
    min_bid_price          bigint,
    fail_count             integer,
    sale_date              text,
    est_market_price       bigint,
    matched_trades         integer,
    confidence             double precision,
    real_acquisition_cost  bigint,
    expected_profit        bigint,
    gap_rate               double precision,
    gap_score              double precision,
    rights_score           double precision,
    liquidity_score        double precision,
    arb_score              double precision,
    grade                  text,
    court                  text    not null default '',
    item_no                text    not null default '',
    doc_id                 text    not null default '',
    market_scope           text    not null default '',
    market_band_low        bigint,
    market_band_high       bigint,
    profit_low             bigint,
    profit_high            bigint,
    market_sample_basis    integer,
    -- 상세 시간축 차트용 개별 실거래 점 [[deal_ym, price], …]. store_rest 가 jsonb 로 미러.
    market_comps           jsonb   not null default '[]'::jsonb,
    -- 이 행이 마지막으로 갱신된 새로고침 시각. replace_all(전량교체)이 이번 run 시각으로
    -- 전부 upsert 후 그보다 오래된 행을 지워 만료(팔림/취하) 매물을 제거하는 데 쓴다.
    refreshed_at           timestamptz not null default now(),
    primary key (court, case_no, item_no)
);

-- 차익 스코어 내림차순 서빙(NULL=시세추정불가는 맨 뒤) 정렬 가속.
create index if not exists auction_scored_arb_idx
    on public.auction_scored_listings (arb_score desc nulls last);

-- 서버 전용 접근: RLS 켜고 정책은 두지 않음 → service key만 통과(anon/authenticated 차단).
alter table public.auction_scored_listings enable row level security;

-- [기존 테이블 마이그레이션 2026-07-13] 상세 차트 실거래 점 컬럼. 이미 있으면 무시(idempotent).
-- 이 한 줄을 Supabase SQL Editor 에서 실행해야 재크롤 후 프로덕션 차트에 파란 실거래 점이 찍힘.
alter table public.auction_scored_listings
    add column if not exists market_comps jsonb not null default '[]'::jsonb;

-- [기존 테이블 마이그레이션 2026-07-24] 매각 개시시각(raw maeHh1 'HHMM') 미러 컬럼.
-- 클라우드 서빙은 raw_listings 가 없어 파생 불가 → run.py 가 미러 직전 주입(store._sale_time_map).
-- 없으면 query.bidding_closed 가 10:00 폴백 가정으로 동작(2026-07-24 Management API 로 적용 완료).
alter table public.auction_scored_listings
    add column if not exists sale_time text not null default '';

-- [기존 테이블 마이그레이션 2026-07-24] 저층(1~2층·지하) 시세 보정 배율(floor_adjust).
-- est/밴드에 이미 곱해진 값의 기록 — UI 정직성 표기·감사 대조용(Management API 로 적용 완료).
alter table public.auction_scored_listings
    add column if not exists floor_mult double precision not null default 1.0;

-- 확인: 아래가 rows=0(테이블 준비됨)으로 나오면 성공. 이관 스크립트가 3,751건을 채웁니다.
select count(*) as rows from public.auction_scored_listings;
