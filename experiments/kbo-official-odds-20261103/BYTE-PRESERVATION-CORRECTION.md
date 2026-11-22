# 가격 파일 바이트 보존 정정

기존 커밋 `9bcbd53bf95ce6c6961b5b88fd43877152d602b6`의 `restored-prices.jsonl` Git blob은 Git의 텍스트 줄바꿈 정규화로 LF를 사용했다. 이 blob의 SHA-256은 `cb556b0fb182eb13de6d22a513b8a04952e5e22d6b31a6b6ca44f543edb647aa`로, 당시 manifest와 분석에 사용한 CRLF 원본 파일 SHA-256 `d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117`과 다르다. 두 파일은 줄바꿈을 LF로 통일했을 때 JSON 내용이 같지만, 해당 커밋의 Git 아카이브나 LF 체크아웃만으로는 원래 고정 바이트와 manifest 검사를 재현할 수 없다.

이후 커밋은 기존 CRLF 원본 바이트를 그대로 Git blob에 재등록하고 해당 파일만 `.gitattributes`의 `-text`로 고정한다. 생성 코드도 운영체제와 무관하게 같은 CRLF 바이트를 기록한다. manifest, 373개 가격 레코드와 수신 시각, 압축된 공식 응답 원문은 바꾸지 않는다. 과거 커밋 자체가 수정된 것은 아니며, 소비자는 이 정정을 포함한 커밋의 blob과 manifest 해시를 확인해야 한다.
