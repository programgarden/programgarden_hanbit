import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 원격(예: Tailscale·SSH 터널)으로 브라우저에서 dev 서버 접근 시 _next/* 자산 cross-origin 허용.
  // 필요하면 본인 원격 호스트명을 넣으세요. 예: ["my-host.example.ts.net"]
  allowedDevOrigins: [],

  // Same-origin API 프록시. 브라우저는 페이지를 받은 web 서버 origin 으로만 API 를
  // 호출하고, web 서버가 같은 컨테이너 안의 백엔드(:8000)로 프록시한다. 이렇게 하면
  // 호스트↔컨테이너 포트 매핑이나 Tailscale 원격(브라우저의 localhost ≠ 호스트)에
  // 무관하게 동작한다. `BACKEND_ORIGIN` 으로 백엔드 주소를 바꿀 수 있다(기본 동일 컨테이너).
  async rewrites() {
    const backend = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";
    return [
      { source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` },
    ];
  },
};

export default nextConfig;
