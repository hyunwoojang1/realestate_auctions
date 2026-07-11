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
    fetched_at       text not null default '',
    primary key (court, case_no, item_no)
);

alter table public.auction_listing_rights enable row level security;

-- 확인: rows=0 이면 준비 완료(크롤 미러가 채웁니다).
select count(*) as rows from public.auction_listing_rights;
