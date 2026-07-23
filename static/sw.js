/* 서비스워커 — PWA 앱 셸 캐싱 (2026-07-23, 홈 로딩 개선 B단계).

   문제: Vercel 서버리스는 유휴 후 콜드 부팅에 실측 58초. 개인 사용 패턴(하루 몇 번)에선
   거의 항상 콜드라, 홈 화면 아이콘을 탭할 때마다 흰 화면을 그 시간만큼 봤다.

   전략(경로별로 다르다 — 데이터 정직성 우선):
     · 탭 페이지 8종(쿼리 없음)  → stale-while-revalidate: 캐시본 즉시 표시 + 백그라운드
       재검증. 새 응답이 **실제로 다를 때만** 페이지에 알림 → base.html 이 갱신 필을 띄운다.
       재검증 실패도 침묵시키지 않는다(failed 통지 → '미검증 캐시본' 표시).
     · 정적 에셋(아이콘·manifest·base.css) → cache-first. base.css 는 콘텐츠 해시(?v=)라
       배포 시 URL 자체가 바뀌어 스테일 위험이 없다(새 버전 적재 시 옛 엔트리 청소).
     · 그 외 전부(쿼리 붙은 목록·물건 상세·/api/*·/export.csv) → 서비스워커 무개입.
       상세·검색 결과는 최신성이 생명이라 캐시로 오래된 가격·기일을 보여주면 안 된다.
     · 같은 오리진 **비GET(관심 토글 등 mutation)** → 탭 캐시 선제 퍼지. PRG 복귀 화면이
       토글 전 캐시본으로 렌더되는 것을 막는다(적대감사 2026-07-23 CRITICAL).

   v2(2026-07-23 적대감사 반영):
     - F1 cached.clone() 을 respondWith 소비 **전에** 떠 둔다 — 소진된 body 의 clone 은
       TypeError 라 재검증 전체가 침묵 사망(캐시 영구 동결·필 영구 미표시)했던 버그.
     - F2 샘플 폴백(X-Data-Source: sample*) 응답은 서빙만 하고 캐시하지 않는다.
     - F3 비GET 요청 시 NAV_CACHE 퍼지(PRG 스테일).
     - F4 재검증 실패 통지(failed) + rendered-at 메타는 비교 전 제거(가변 필드 오탐 방지).
     - 쿼리 페이지엔 알림 미전송(개입 범위와 1:1) · base.css 옛 버전 엔트리 청소.
   버전을 올리면 activate 에서 옛 캐시가 전부 삭제된다(v1 의 동결 캐시 포함). */
const VERSION = 'v2';
const NAV_CACHE = 'nav-' + VERSION;
const ASSET_CACHE = 'asset-' + VERSION;

/* ⚠️ src/web.py 의 실제 라우트와 1:1 이어야 한다 — tests/test_pwa_shell.py 가
   이 배열을 파싱해 각 경로가 200을 주는지 검사한다(라우트 드리프트 가드). */
const NAV_PATHS = ['/', '/find', '/map', '/calendar', '/watchlist', '/stats', '/guide', '/methodology'];
const ASSETS = ['/icon-192.png', '/icon-512.png', '/apple-touch-icon.png', '/manifest.webmanifest'];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(ASSET_CACHE)
      .then(function (c) { return c.addAll(ASSETS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys
          .filter(function (k) { return k !== NAV_CACHE && k !== ASSET_CACHE; })
          .map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (req.method !== 'GET') {
    /* (F3) mutation(관심 토글 POST 등) → 탭 캐시 선제 퍼지. 서버는 302 로 '/'나
       '/watchlist' 로 되돌리는데(PRG), 퍼지 없이는 그 GET 이 토글 **전** 캐시본을 서빙해
       "조작이 실패한 것처럼" 보이고, 재탭하면 서버 상태가 반대로 뒤집힌다. */
    e.waitUntil(caches.open(NAV_CACHE).then(function (c) {
      return Promise.all(NAV_PATHS.map(function (p) { return c.delete(p); }));
    }));
    return;
  }

  /* base.css — 해시 버전 URL이라 cache-first 가 안전(새 배포 = 새 URL). */
  if (url.pathname === '/base.css' || ASSETS.indexOf(url.pathname) !== -1) {
    e.respondWith(cacheFirst(req));
    return;
  }

  if (req.mode === 'navigate' && NAV_PATHS.indexOf(url.pathname) !== -1 && !url.search) {
    e.respondWith(staleWhileRevalidate(e, req, url.pathname));
  }
  /* 그 외는 개입하지 않는다 — 브라우저 기본 네트워크 동작. */
});

function cacheFirst(req) {
  return caches.match(req).then(function (hit) {
    if (hit) return hit;
    return fetch(req).then(function (res) {
      if (res.ok) {
        var copy = res.clone();
        caches.open(ASSET_CACHE).then(function (c) {
          c.put(req, copy).then(function () {
            /* base.css 는 배포마다 ?v= 가 바뀐다 — 새 버전을 넣었으면 옛 버전 엔트리를
               청소해 영구 축적을 막는다(다른 에셋은 URL 고정이라 해당 없음). */
            var u = new URL(req.url);
            if (u.pathname === '/base.css') {
              c.keys().then(function (ks) {
                ks.forEach(function (k) {
                  if (new URL(k.url).pathname === '/base.css' && k.url !== req.url) c.delete(k);
                });
              });
            }
          });
        });
      }
      return res;
    });
  });
}

/* rendered-at 메타는 렌더마다 바뀐다(정직성 표식) — 실데이터 변경만 감지하도록 비교 전 제거. */
function stripVolatile(t) {
  return t.replace(/<meta name="rendered-at"[^>]*>/, '');
}

function staleWhileRevalidate(event, req, pathname) {
  return caches.open(NAV_CACHE).then(function (cache) {
    return cache.match(req).then(function (cached) {
      /* (F1) 비교용 텍스트를 respondWith 가 body 를 소진하기 **전에** 떠 둔다.
         종전엔 fetch 완료 후 cached.clone() → 이미 소진된 body 라 TypeError →
         빈 catch 가 삼켜 cache.put·알림이 영원히 실행되지 않았다(캐시 영구 동결). */
      var cachedText = cached ? cached.clone().text() : Promise.resolve(null);

      var refresh = fetch(req).then(function (res) {
        var type = res.headers.get('content-type') || '';
        if (!res.ok || type.indexOf('text/html') === -1) {
          if (cached) notifySamePath(pathname, { failed: true });
          return res;
        }
        /* (F2) DB 장애 시 서버는 500이 아니라 **샘플 데이터 200** 으로 폴백한다
           (X-Data-Source: sample*). 캐시에 넣으면 합성 매물이 다음 실행의 첫 화면이 되고
           라이브 캐시본을 덮어쓴다 — 서빙만 하고 캐시 오염은 금지. */
        var src = res.headers.get('X-Data-Source') || '';
        if (src.indexOf('sample') === 0) {
          if (cached) notifySamePath(pathname, { failed: true });
          return res;
        }
        var freshCopy = res.clone();
        return Promise.all([cachedText, res.clone().text()]).then(function (bodies) {
          /* (F1) put 은 비교 결과와 무관하게 무조건 — 갱신이 우선, 알림은 부차. */
          return cache.put(req, freshCopy).then(function () {
            if (bodies[0] !== null) {
              var changed = stripVolatile(bodies[0]) !== stripVolatile(bodies[1]);
              notifySamePath(pathname, { changed: changed });
            }
            return res;
          });
        });
      });

      if (cached) {
        /* 캐시본 즉시 반환 — 재검증은 백그라운드. 실패(오프라인·콜드 타임아웃·네트워크 예외)도
           침묵시키지 않는다(F4) — 페이지가 '미검증 캐시본' 상태를 표시할 수 있게. */
        event.waitUntil(refresh.catch(function () {
          notifySamePath(pathname, { failed: true });
        }));
        return cached;
      }
      /* 첫 방문(캐시 없음) — 네트워크. 실패 시 브라우저 기본 오류. */
      return refresh;
    });
  });
}

function notifySamePath(pathname, extra) {
  self.clients.matchAll({ type: 'window' }).then(function (cs) {
    cs.forEach(function (c) {
      try {
        var u = new URL(c.url);
        /* 쿼리 페이지(/find?q=, /?all=1)는 SW 무개입 경로 — 비교·캐시된 적 없는 화면에
           알림을 보내면 오탐이다. 개입 범위(!url.search)와 알림 대상을 1:1 로 맞춘다. */
        if (u.pathname === pathname && u.search === '') {
          c.postMessage(Object.assign({ type: 'nav-fresh' }, extra));
        }
      } catch (err) { /* URL 파싱 실패 클라이언트는 건너뜀 */ }
    });
  });
}
