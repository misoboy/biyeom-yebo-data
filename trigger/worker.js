// 비염예보 데이터 빌드 방아쇠.
// GitHub Actions 의 schedule 트리거가 이 계정에서 6~9시간씩 늦거나 아예 안 돌아서(2026-11-26 확인),
// Cloudflare 예약 실행이 매시 35·55분에 workflow_dispatch 를 대신 보낸다. 데이터 수집·배포는 그대로 GitHub Actions 가 한다.
const REPO = "misoboy/biyeom-yebo-data";
const WORKFLOW = "build.yml";

async function dispatch(env) {
  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "biyeom-yebo-trigger",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status !== 204) {
    // 토큰 만료(401)·권한 부족(403)이면 여기서 실패한다. Cloudflare 대시보드 로그에서 보인다.
    throw new Error(`dispatch failed: ${res.status} ${(await res.text()).slice(0, 200)}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
};
