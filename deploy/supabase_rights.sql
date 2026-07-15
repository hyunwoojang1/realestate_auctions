-- 권리·기일 요지(법원 물건상세 크롤분) 클라우드 미러 — Vercel 상세 페이지 서빙용.
-- 실행: Supabase Dashboard(econ 프로젝트 trajmfklbyarbkiljogj) → SQL Editor → Run.
-- econ 기존 테이블 무접촉(auction_ prefix 신규 1개). RLS on + 정책 없음 = service key 전용.

create table if not exists public.auction_listing_rights (
    court            text not null default '',
    case_no          text not null,
    item_no          text not null default '',
    surviving_rights text not null default '',   -- 매수인에게 인수되는 권리(명세서 원문 요지)
    senior_lien      text not null default '',   -- 최선순위 설정 내역(말소기준)
    lien_note        text not null default '',   -- 유치권·법정지상권 등
    remark           text not null default '',   -- 명세서 비고
    claim_amt        bigint,                     -- 청구금액(원)
    demand_end       text not null default '',   -- 배당요구종기
    spec_write_ymd   text not null default '',   -- 명세서 작성일
    court_dept       text not null default '',   -- 담당 경매계
    schedule         text not null default '[]', -- 기일 역사 JSON [{ymd,kind,result,price}]
    appraisal_notes  text not null default '[]', -- 감정평가 요항점 JSON [{label,text}]
    fetched_at       text not null default '',
    primary key (court, case_no, item_no)
);

alter table public.auction_listing_rights enable row level security;

-- [기존 테이블 마이그레이션 2026-07-13] 감정평가 요항점 컬럼(이미 있으면 무시).
alter table public.auction_listing_rights
    add column if not exists appraisal_notes text not null default '[]';

-- [2026-07-13 개편] 물건 사진 썸네일(base64 JPEG) — 상세 히어로 클라우드 서빙용.
-- 용량 억제 위해 시세추정 가능 물건에만 소수 저장(crawl_rights). RLS on + 정책 없음.
create table if not exists public.auction_listing_photos (
    court      text not null default '',
    case_no    text not null,
    item_no    text not null default '',
    seq        integer not null default 0,
    thumb_b64  text not null,
    fetched_at text not null default '',
    primary key (court, case_no, item_no, seq)
);
alter table public.auction_listing_photos enable row level security;

-- [2026-07-15] 네이버 KB시세·호가 매핑 미러(store.DDL_NAVER 등가) — 목록 배지·상세 차익 서빙용.
-- status: matched_kb | matched_ask | no_kb | no_match | no_coord. RLS on + 정책 없음 = service key 전용.
create table if not exists public.auction_naver_prices (
    court        text not null default '',
    case_no      text not null,
    item_no      text not null default '',
    status       text not null default '',
    complex_no   text default '',
    complex_name text default '',
    area_no      text default '',
    match_conf   text default '',
    kb_low       integer,               -- 하한가(원)
    kb_avg       integer,               -- 일반가(원) = KB '시세'
    kb_high      integer,               -- 상한가(원)
    lease_avg    integer,               -- 전세 일반가(원)
    ask_min      integer,               -- 호가 최저(원)
    ask_max      integer,               -- 호가 최고(원)
    ask_count    integer default 0,
    base_ymd     text default '',
    fetched_at   text not null default '',
    primary key (court, case_no, item_no)
);
alter table public.auction_naver_prices enable row level security;

-- 고아 권리 자동정리 RPC — scored 에 대응 물건이 없는 rights 를 서버측 단일 쿼리로 삭제.
-- run.py 가 풀스냅샷 새로고침 후 store_rest.prune_rights()로 호출(rights 무한누적 방지).
create or replace function public.prune_auction_orphan_rights()
returns integer language plpgsql security definer set search_path = public as $$
declare deleted integer;
begin
    delete from public.auction_listing_rights r
    where not exists (
        select 1 from public.auction_scored_listings s
        where s.court = r.court and s.case_no = r.case_no and s.item_no = r.item_no
    );
    get diagnostics deleted = row_count;
    return deleted;
end $$;

-- 확인: rows=0 이면 준비 완료(크롤 미러가 채웁니다).
select count(*) as rows from public.auction_listing_rights;
