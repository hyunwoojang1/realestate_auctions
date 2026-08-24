"""물건사진 오브젝트 스토리지 — Cloudflare R2(기본) / Supabase Storage(레거시) 듀얼 백엔드.

배경: 사진을 base64로 DB에 넣으면 Supabase 무료티어 DB 500MB를 넘긴다(실측 사진만 ~500MB).
바이너리를 오브젝트 스토리지로 빼고 DB에는 공개 URL만 저장 → DB 경량화 + 사진 대상 확대.

2026-08-05 R2 추가: Supabase 무료 파일스토리지 1GB를 초과(실측 1,858MB·38,101장)해 공정사용
제한 통보를 받았다. R2는 저장 10GB·이그레스 무제한이 무료라 "과거 사진을 지우지 않고 계속
쌓는다"는 요구와 맞는다. 백엔드는 환경변수로 선택되며, **DB에는 항상 완성된 절대 URL을
저장**하므로 이전 중에도 옛 Supabase URL과 새 R2 URL이 섞여 그대로 렌더된다(무중단).

인증:
- R2       — S3 호환 API + AWS SigV4. boto3를 쓰지 않는다(botocore 포함 100MB↑ → Vercel 함수
             225MB 한도 위협. requirements.txt는 서빙과 공유된다). 서명은 표준 라이브러리로 충분.
- Supabase — store_rest 와 동일(SUPABASE_SECRET_KEY). 자동 선택되지 않는다(backend() 참고).

⚠️ 공개 버킷 설계의 전제 (2026-08-05 보안 리뷰에서 명시 요구):
버킷은 **무인증 공개**이고 오브젝트 키가 `sha1(법원|사건번호|물건번호|seq)` 라, 사건번호를 아는
사람은 URL을 계산해 낼 수 있다(법원명·사건번호·물건번호는 전부 법원이 공개하는 값이다).
이게 허용되는 근거는 **여기 올라가는 사진이 courtauction.go.kr 이 로그인 없이 공개하는 물건사진과
동일 원천**이라는 것뿐이다. 업로드 전 Pillow 재인코딩으로 EXIF 는 사실상 제거된다.
⛔ 비공개 원천의 사진(현황조사서 내부 자료, 임차인 관련 촬영본 등)을 이 파이프라인에 넣으려면
이 전제가 깨지므로 **공개 버킷 설계부터 재검토**해야 한다. 키를 추측 불가하게 바꾸는 것만으로는
부족하다(보안이 URL 비밀성에만 기대게 된다).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from datetime import UTC, datetime
from urllib.parse import quote

logger = logging.getLogger(__name__)

_R2_REGION = "auto"      # R2는 리전 개념이 없다 — SigV4 스코프에 쓰는 고정값
_R2_SERVICE = "s3"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------- 설정/백엔드 선택

def _r2_cfg():
    return (os.environ.get("R2_ACCOUNT_ID", "").strip(),
            os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
            os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
            os.environ.get("R2_BUCKET", "auction-photos").strip(),
            os.environ.get("R2_PUBLIC_BASE", "").strip().rstrip("/"))


def _supabase_cfg():
    return (os.environ.get("SUPABASE_URL", "").rstrip("/"),
            os.environ.get("SUPABASE_SECRET_KEY", ""),
            os.environ.get("SUPABASE_PHOTOS_BUCKET", "auction-photos"))


def _r2_ready() -> bool:
    """R2 4종 + 공개 도메인이 모두 있어야 '준비됨'.

    R2_PUBLIC_BASE 를 필수로 두는 이유: 없으면 업로드는 성공하는데 DB에 넣을 URL을 만들 수 없다.
    그대로 진행하면 '사진은 올라갔는데 화면엔 안 나오는' 침묵실패가 된다 — 아예 시작을 막는다.
    """
    acct, akid, skey, bucket, pub = _r2_cfg()
    return bool(acct and akid and skey and bucket and pub)


def backend() -> str:
    """'r2' | 'supabase' | '' (미설정). AUCTION_PHOTO_BACKEND 로만 supabase 를 선택할 수 있다.

    **Supabase 사진 버킷은 자동 선택하지 않는다.** 2026-08-05 무료 1GB 초과로 R2 로 전량
    이전했는데, R2 키가 하나라도 빠졌을 때 자동으로 여기 흘러들어가면 업로드는 계속 성공하므로
    (자격증명이 .env 에 함께 살아있다) 크롤은 exit 0 으로 끝나고 아무도 못 알아챈다 — 경고 로그를
    찍어도 스케줄러는 실패로 감지하지 못한다. 그래서 '미설정'으로 떨어뜨려 상위의 안전망
    (사진 저장 생략 + 0장이면 exit 3)이 작동하게 한다. 정말 쓰려면 AUCTION_PHOTO_BACKEND=supabase.
    """
    forced = os.environ.get("AUCTION_PHOTO_BACKEND", "").strip().lower()
    if forced in ("r2", "supabase"):
        return forced
    return "r2" if _r2_ready() else ""


def enabled() -> bool:
    b = backend()
    if b == "r2":
        return _r2_ready()
    if b == "supabase":
        url, key, _ = _supabase_cfg()
        return bool(url and key)
    return False


def split_stale_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """사진 미러 payload 를 (올릴 것, 막을 것) 으로 가르고, 올릴 것에서 base64 를 벗긴다.

    로컬 listing_photos 전량을 PK 기준으로 클라우드에 **덮어쓰는** 미러가 있다. 클라우드는
    upsert-only(삭제 없음)라 잘못된 행을 올리면 조용히 그 자리를 오염시킨다. 세 종류를 막는다:

    1) 옛 Supabase Storage URL — 이미 R2 로 옮긴 클라우드 행을 죽은 URL 로 되돌린다.
       원본 버킷은 이미 삭제됐으므로 복구 불가(=사진 깨짐).
    2) `photo_url` 이 빈 행 — 덮어쓰면 클라우드의 멀쩡한 R2 URL 이 빈 값이 된다(사진 사라짐).
       `AUCTION_ALLOW_BASE64_PHOTOS=1` 로 base64 만 저장된 행이 정확히 이 모양이다.
    3) `thumb_b64` 값 자체 — 클라우드 테이블에 `thumb_b64` 컬럼이 실재해서, 그대로 올리면
       **base64 blob 이 Supabase Postgres 에 쌓인다.** 그게 이번 이전의 발단이 된 DB 용량
       초과 사고를 클라우드에서 그대로 재현하는 경로다. 올리는 행은 항상 빈 문자열로 덮는다.

    (1)만 막던 종전 구현의 구멍을 2026-08-05 리뷰(신규 에이전트)가 잡아냈다. 되돌릴 수 없는
    사고를 막는 가드라 호출부 인라인이 아니라 테스트 가능한 순수 함수로 둔다.
    """
    clean, blocked = [], []
    for r in rows:
        url = r.get("photo_url") or ""
        if "supabase.co/storage" in url or not url:
            blocked.append(r)
            continue
        clean.append({**r, "thumb_b64": ""} if r.get("thumb_b64") else r)
    return clean, blocked


def object_path(court: str, case_no: str, item_no: str, seq: int) -> str:
    """ASCII 결정키(한글/특수문자 회피) — 같은 물건·seq는 항상 같은 경로(재크롤 덮어쓰기).

    백엔드가 바뀌어도 키는 동일하다 → Supabase→R2 이전이 멱등(중단·재실행 안전).
    """
    h = hashlib.sha1(f"{court}|{case_no}|{item_no}|{seq}".encode()).hexdigest()
    return f"{h}.jpg"


def public_url(path: str) -> str:
    if backend() == "r2":
        _, _, _, _, pub = _r2_cfg()
        return f"{pub}/{path}"
    url, _, bucket = _supabase_cfg()
    return f"{url}/storage/v1/object/public/{bucket}/{path}"


# ---------------------------------------------------------------- AWS SigV4 (R2용)

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    k = _sign(f"AWS4{secret}".encode(), datestamp)
    k = _sign(k, region)
    k = _sign(k, service)
    return _sign(k, "aws4_request")


def sigv4_headers(method: str, host: str, canonical_uri: str, payload: bytes,
                  akid: str, secret: str, content_type: str = "",
                  now: datetime | None = None, region: str = _R2_REGION,
                  service: str = _R2_SERVICE, content_sha_header: bool = True) -> dict[str, str]:
    """단건 요청용 SigV4 서명 헤더. 쿼리스트링 없는 PUT/HEAD/GET 전용.

    now/region/service/content_sha_header 를 열어둔 것은 **AWS 공식 테스트 벡터
    (aws-sig-v4-test-suite get-vanilla)로 알고리즘 자체를 대조**하기 위함이다. 자기 구현을
    자기 출력으로 검증하면 순환이라, 외부 정답과 맞춰본다. R2 실사용 경로는 기본값 그대로다.
    """
    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest() if payload else _EMPTY_SHA256

    headers = {"host": host, "x-amz-date": amz_date}
    if content_sha_header:
        headers["x-amz-content-sha256"] = payload_hash
    if content_type:
        headers["content-type"] = content_type
    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))

    canonical_request = "\n".join(
        [method, canonical_uri, "", canonical_headers, signed_names, payload_hash])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope,
         hashlib.sha256(canonical_request.encode()).hexdigest()])
    signature = hmac.new(_signing_key(secret, datestamp, region, service),
                         string_to_sign.encode(), hashlib.sha256).hexdigest()

    out = {k: v for k, v in headers.items() if k != "host"}
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={akid}/{scope}, "
                            f"SignedHeaders={signed_names}, Signature={signature}")
    return out


_SESSION = None
_SESSION_LOCK = threading.Lock()
_POOL_SIZE = None       # configure_pool() 로 명시 지정. None 이면 PHOTO_POOL_SIZE 또는 기본 48.


def configure_pool(n: int) -> None:
    """세션 생성 **전에** 풀 크기를 못 박는다.

    환경변수만으로 넘기면 호출 순서에 걸린다 — 이전 스크립트가 `ensure_bucket()` 을 먼저 부르면
    그 시점에 세션이 기본값으로 굳어버려, 뒤늦게 올린 값이 반영되지 않는다(2026-08-05 리뷰 지적).
    이미 세션이 만들어진 뒤에 부르면 경고만 남기고 무시한다 — 조용히 다른 값이 적용된 척하지 않는다.
    """
    global _POOL_SIZE
    with _SESSION_LOCK:
        if _SESSION is not None:
            logger.warning("configure_pool(%s) 무시 — 세션이 이미 생성됨(풀=%s)", n, _POOL_SIZE)
            return
        _POOL_SIZE = int(n)


def session():
    """커넥션 풀을 갖춘 공용 세션.

    `requests.get/post` 는 호출마다 Session 을 새로 만든다 = 사진 한 장마다 TCP+TLS 핸드셰이크.
    한두 장이면 무시할 만하지만 **3.8만 장 이전에서는 이게 지배적 비용**이다(2026-08-05 실측:
    초반 600장/분 → 133장/분으로 붕괴). 풀 크기는 워커 수 이상이어야 커넥션 대기가 안 생긴다.

    32개 워커 스레드가 공유하므로 **락으로 이중 생성을 막는다**(락 없는 지연 초기화는 첫 요청
    폭주 시 세션을 여러 개 만들어 버린다 — 풀이 쪼개져 최적화 효과가 깎인다).
    `max_retries=0` 은 의도적이다: 호출부(크롤·이전 스크립트)가 멱등 재실행으로 재시도를
    책임지므로, 여기서 재시도하면 실패가 두 겹으로 늘어나고 중단 응답이 느려진다.
    """
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import requests  # noqa: PLC0415
            from requests.adapters import HTTPAdapter  # noqa: PLC0415
            n = _POOL_SIZE or int(os.environ.get("PHOTO_POOL_SIZE", "48"))
            s = requests.Session()
            s.mount("https://", HTTPAdapter(pool_connections=n, pool_maxsize=n, max_retries=0))
            _SESSION = s
    return _SESSION


def _r2_request(method: str, path: str, payload: bytes = b"", content_type: str = "",
                timeout: int = 30):
    """R2 S3 API 호출(path-style: /{bucket}/{key}). 반환=requests.Response."""
    acct, akid, skey, bucket, _ = _r2_cfg()
    host = f"{acct}.r2.cloudflarestorage.com"
    # 경로는 "/" 로 쪼개 **세그먼트별로** 인코딩한다(SigV4 스펙). 통째로 quote 하면 "/" 가
    # %2F 가 되는데 Cloudflare 가 이를 "/" 로 정규화해 서명을 다시 계산하므로 403
    # SignatureDoesNotMatch 가 난다(2026-08-24 백업 도입 때 실측 — 사진 키는 sha1 평면이라
    # "/" 가 없어서 그동안 안 밟혔던 버그).
    segs = [bucket] + (path.split("/") if path else [])
    canonical_uri = "/" + "/".join(quote(s, safe="") for s in segs)
    headers = sigv4_headers(method, host, canonical_uri, payload, akid, skey, content_type)
    return session().request(method, f"https://{host}{canonical_uri}",
                             headers=headers, data=payload or None, timeout=timeout)


# ---------------------------------------------------------------- 공개 API

def ensure_bucket() -> bool:
    """버킷 접근 가능 여부 확인. Supabase는 없으면 생성(멱등), R2는 확인만."""
    if backend() == "r2":
        if not _r2_ready():
            logger.warning("R2 설정 불완전 — R2_ACCOUNT_ID/ACCESS_KEY_ID/SECRET_ACCESS_KEY/"
                           "BUCKET/PUBLIC_BASE 5종 필요")
            return False
        try:
            r = _r2_request("HEAD", "", timeout=15)
        except Exception as e:  # noqa: BLE001 — 네트워크 오류는 '사용 불가'로 강등(크롤은 계속)
            logger.warning("R2 버킷 확인 실패: %s", type(e).__name__)
            return False
        if r.status_code == 200:
            return True
        # R2 버킷 생성은 대시보드에서 한다 — 공개 접근(r2.dev/커스텀 도메인) 설정이 S3 API로
        # 불가능해서, API로 만들면 '업로드는 되는데 안 보이는' 상태가 된다.
        logger.warning("R2 버킷 '%s' 접근 불가 HTTP %s — 대시보드에서 버킷 생성·공개 설정 확인",
                       _r2_cfg()[3], r.status_code)
        return False

    import requests  # noqa: PLC0415
    url, key, bucket = _supabase_cfg()
    if not (url and key):
        return False
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{url}/storage/v1/bucket/{bucket}", headers=h, timeout=15)
    if r.status_code == 200:
        return True
    r = requests.post(f"{url}/storage/v1/bucket", headers={**h, "Content-Type": "application/json"},
                      json={"id": bucket, "name": bucket, "public": True,
                            "allowed_mime_types": ["image/jpeg"], "file_size_limit": 2_000_000},
                      timeout=15)
    ok = r.status_code in (200, 201) or "already exists" in r.text.lower()
    if not ok:
        logger.warning("Storage 버킷 생성 실패 HTTP %s %s", r.status_code, r.text[:120])
    return ok


def _upload_result(r, path: str, log_prefix: str) -> str | None:
    """업로드 응답 판정 공통부: 200/201 이면 공개 URL, 아니면 실패 로그 후 None.

    r2/supabase 는 요청을 보내는 방식이 다르지만(R2 는 예외를 잡아 None 으로 강등하고,
    Supabase 는 그대로 전파 — 호출부에서 유지) 응답 판정 로직 자체는 동일해 여기로 묶는다.
    """
    if r.status_code in (200, 201):
        return public_url(path)
    logger.warning("%s 실패 HTTP %s %s", log_prefix, r.status_code, r.text[:120])
    return None


def upload_bytes(jpeg: bytes, path: str) -> str | None:
    """지정 경로에 JPEG 업로드하고 공개 URL 반환. 실패 시 None. (이전 스크립트가 재사용)"""
    if not jpeg or not enabled():
        return None
    if backend() == "r2":
        try:
            r = _r2_request("PUT", path, payload=jpeg, content_type="image/jpeg")
        except Exception as e:  # noqa: BLE001
            logger.warning("R2 업로드 예외 %s", type(e).__name__)
            return None
        return _upload_result(r, path, "R2 업로드")

    url, key, bucket = _supabase_cfg()
    r = session().post(f"{url}/storage/v1/object/{bucket}/{path}",
                       headers={"apikey": key, "Authorization": f"Bearer {key}",
                                "Content-Type": "image/jpeg", "x-upsert": "true"},
                       data=jpeg, timeout=30)
    return _upload_result(r, path, "사진 업로드")


def upload_photo(jpeg: bytes, court: str, case_no: str, item_no: str, seq: int) -> str | None:
    """JPEG 바이트를 업로드하고 공개 URL 반환. 실패 시 None. (크롤러 호출부 — 시그니처 불변)"""
    return upload_bytes(jpeg, object_path(court, case_no, item_no, seq))


def upload_blob(data: bytes, path: str,
                content_type: str = "application/octet-stream") -> str | None:
    """임의 바이트 업로드(DB 백업 zip 등) — R2 백엔드 전용. 성공 시 공개 URL, 실패 시 None.

    Supabase storage 폴백을 태우지 않는 이유: 무료 1GB 한도가 사진용으로도 빠듯해
    (2026-08-05 R2 전환 사유) 수백 MB 백업이 들어가면 사진 업로드까지 같이 죽는다.
    (2026-08-24 감사 C-1 백업 자동화에서 도입 — scripts/backup_db.py 가 호출)
    """
    if not data or not _r2_ready():
        return None
    try:
        r = _r2_request("PUT", path, payload=data, content_type=content_type)
    except Exception as e:  # noqa: BLE001
        logger.warning("R2 blob 업로드 예외 %s", type(e).__name__)
        return None
    return _upload_result(r, path, "R2 blob 업로드")
