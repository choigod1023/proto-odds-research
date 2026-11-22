export function commentaryMethod(status) {
  if (status === "llm_rewritten" || status === "llm_cache") return "LLM 문장 편집";
  if (String(status || "").startsWith("template_")) return "검증 템플릿 · LLM 대체 동작";
  if (status === "disabled_no_key") return "검증 템플릿 · LLM 키 미설정";
  return "경기 해설";
}
