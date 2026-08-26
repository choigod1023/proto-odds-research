import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "../..");
let server;
let schedule;

before(async () => {
  server = await createServer({
    root: webRoot,
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "silent",
  });
  schedule = await server.ssrLoadModule("/src/pages/Markets.jsx");
});

after(async () => {
  await server?.close();
});

test("timezone이 명시된 kickoff_at을 date보다 우선하고 시작 즉시 제외한다", () => {
  const game = {
    status: "경기전",
    kickoff_at: "2026-08-26T11:00:00+09:00",
    date: "08.26(수) 23:00",
  };
  const source = { generated_at: "2026-08-26T01:40:00Z" };
  const kickoff = Date.parse("2026-08-26T11:00:00+09:00");

  assert.equal(schedule.marketKickoffTime(game, source, kickoff - 1), kickoff);
  assert.equal(schedule.isUpcomingScheduledGame(game, source, kickoff - 1), true);
  assert.equal(schedule.isUpcomingScheduledGame(game, source, kickoff), false);
});

test("KST 12:40에는 date 기반 11:00·11:30을 숨기고 18:00만 남긴다", () => {
  const source = { generated_at: "2026-08-26T01:48:14Z" };
  const now = Date.parse("2026-08-26T12:40:00+09:00");
  const game = (time) => ({ status: "경기전", date: `08.26(수) ${time}` });

  assert.equal(schedule.isUpcomingScheduledGame(game("11:00"), source, now), false);
  assert.equal(schedule.isUpcomingScheduledGame(game("11:30"), source, now), false);
  assert.equal(schedule.isUpcomingScheduledGame(game("18:00"), source, now), true);
  assert.equal(
    schedule.marketKickoffTime(game("18:00"), { year: 2026 }, now),
    Date.parse("2026-08-26T18:00:00+09:00"),
    "date fallback은 브라우저 연도가 아니라 명시된 source year를 써야 한다",
  );
});

test("오늘·내일 및 연말연초를 KST 날짜와 인접 연도로 해석한다", () => {
  const dec31 = Date.parse("2026-12-31T23:59:00+09:00");
  const jan1 = Date.parse("2027-01-01T00:00:00+09:00");
  assert.equal(schedule.marketKstDateKey(dec31), "2026-12-31");
  assert.equal(schedule.marketKstDateKey(dec31, 1), "2027-01-01");

  const nextYear = { status: "경기전", date: "01.01(금) 00:00" };
  const decemberSource = { generated_at: "2026-12-31T14:50:00Z" };
  assert.equal(schedule.marketKickoffTime(nextYear, decemberSource, dec31), jan1);
  assert.equal(schedule.isUpcomingScheduledGame(nextYear, decemberSource, dec31), true);
  assert.equal(schedule.isUpcomingScheduledGame(nextYear, decemberSource, jan1), false);

  const previousYear = { status: "경기전", date: "12.31(목) 23:59" };
  const januarySource = { generated_at: "2026-12-31T15:10:00Z" };
  assert.equal(
    schedule.marketKickoffTime(previousYear, januarySource, jan1 + 10 * 60 * 1000),
    dec31,
  );
});

test("연도·timezone·날짜를 확정할 수 없으면 예정 행을 fail-close한다", () => {
  const now = Date.parse("2026-08-26T12:40:00+09:00");
  assert.equal(
    schedule.isUpcomingScheduledGame(
      { status: "배당대기", date: "08.26(수) 18:00" }, {}, now,
    ),
    false,
  );
  assert.equal(
    schedule.isUpcomingScheduledGame(
      { status: "경기전", kickoff_at: "2026-08-26T18:00:00", date: "08.26(수) 18:00" },
      { year: 2026 }, now,
    ),
    false,
    "timezone 없는 kickoff_at을 브라우저 로컬시각으로 추측하거나 date로 우회하면 안 된다",
  );
  assert.equal(
    schedule.isUpcomingScheduledGame(
      { status: "경기전", date: "02.30(월) 18:00" }, { year: 2026 }, now,
    ),
    false,
  );
  assert.equal(
    schedule.isUpcomingScheduledGame(
      { status: "경기전", date: "08.26(수) 18:00" },
      { year: 2025, generated_at: "2026-08-26T01:40:00Z" }, now,
    ),
    false,
    "source year와 생성 시각의 KST 연도가 충돌하면 한쪽을 임의로 고르면 안 된다",
  );
  assert.equal(schedule.MARKET_CLOCK_MS, 30_000);
});
