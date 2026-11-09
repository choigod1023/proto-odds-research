import assert from "node:assert/strict";
import test from "node:test";
import { attachTimeline, fixtureTimelinePath, readFixtureTimeline } from "../src/lib/live-feed.js";

test("timeline queries select the exact provider event and fall back only when needed", async (t) => {
  const score = { source: "feed", game_id: "A", start: "2026-11-09T12:00:00Z",
    md: "11.09", home: "홈", away: "원정" };
  const correct = { ...score, timeline: [{ text: "골" }] };
  const wrong = { ...correct, game_id: "B" };
  const path = fixtureTimelinePath(score);
  const calls = [];
  let status = 404;
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    if (url.includes("/api/fixture-timeline")) return new Response("null", { status });
    return Response.json({ games: [wrong, correct] });
  });

  assert.deepEqual(await readFixtureTimeline(path), correct);
  assert.equal(calls.length, 2);
  assert.match(calls[1], /\/api\/live-scores/);
  assert.equal(attachTimeline(score, wrong), score);
  assert.deepEqual(attachTimeline(score, correct), correct);

  status = 200; calls.length = 0;
  assert.equal(await readFixtureTimeline(path), null);
  assert.equal(calls.length, 1);

  status = 503; calls.length = 0;
  await assert.rejects(readFixtureTimeline(path), { status: 503 });
  assert.equal(calls.length, 1);
});
