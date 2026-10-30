# TIPSTER: 군중·확률 결합 문헌과 VOTES 출처 감사

검증일: 2026-10-30. 부모 작업의 조건부 2폴·억지 채우기 없음·연구 손실 제한 OFF를 유지한다. 운영 정책 변경, 모델 학습, ROI 계산은 수행하지 않았다. AGENTS.md 및 연구 범위 정정 문서를 읽었다. 기존에 격리된 부모 작업 폴더 안에서 지정된 `.local-test/TIPSTER`에만 작성했다.

## 확인 결과

VOTES는 597개 고유 경기, 635개 원천 추천이며 strict 508경기와 겹치지 않는 NBA 연장 포함 추가군 89경기로 구성된다. 560경기는 1표, 36경기는 2표, 1경기는 3표다. 복수 작성자 경기는 37개(6.20%)에 불과하다. 동률은 17경기다. 따라서 이 자료 전체를 다수의 독립 전문가가 투표한 대규모 군중 자료로 표현하면 안 된다.

8개 출처의 추천 수는 guidesyj.tistory.com 175, sportsanalyst1.tistory.com 106, golfmasterj.tistory.com 104, Pickswise 105, TonysPicks 90, Miky938 Tips 28, Betblog 21, Covers 6이다. 출처·작성자 조합 라벨은 25개이며 실제 독립 개인 수를 뜻하지 않는다. 작성자별 상세 건수는 feature-audit.json에 보존했다. 예를 들어 행복단비 175, sportsanalyst1 106, Tony Tellez 79, 마스터제이 67이다. golfmasterj의 여러 에디터 라벨도 독립 인물로 인증되지 않았다.

동일 경기 안에서 출처·작성자 키가 반복된 경우와 상충 작성자 제외 기록은 각각 0이다. 하지만 MLB:823538의 Tony Tellez와 Tonys Picks Staff는 같은 문서 SHA256(ca95290adc7767743a455ae674235a1ae447418585e363d18f944ac12948ab65)을 공유하고 모두 away를 추천한다. 작성자 라벨이 다르다는 이유만으로 독립 두 표로 인정하면 안 되는 구체적 사례다. 이 감사에서는 원본 표를 수정하지 않았다.

재사용 URL 그룹은 96개, 문서 해시 그룹은 86개다. 한 문서가 여러 경기를 소개하는 경우가 포함되므로 이 수치는 복제 건수가 아니다. 서로 다른 작성자 라벨을 포함한 동일 해시 그룹은 위 1개다. 가장 많이 겹친 작성자 조합은 golfmasterj의 마스터제이와 guidesyj의 행복단비로 16경기 중 9경기 방향이 일치한다(56.25%). 나머지 조합은 최대 5경기 중첩이며, 1~2경기에서 100% 일치는 복제 증거로 사용할 수 없다. 추천 방향 일치에는 시장 인기팀과 경기 선택 편향이 섞인다. 본문·수정 이력·초기 게시 순서가 없어서 의미 기반 복제, 계정 간 동일인, 조건부 오류 상관은 검증하지 못했다.

## 게시 시각과 수집 증거

635개 추천 중 시간대가 있는 게시 시각은 591개다. 그중 저장된 kickoff 이전 표기는 585개, 같거나 이후 표기는 6개다. 28개는 게시 시각이 없고 16개는 날짜만 있어 같은 날의 선후관계를 단정할 수 없다. 날짜만 있는 값을 임의로 자정이나 한국시간으로 해석하지 않았다. '경기 전' 585개도 게시자 주장과 저장 kickoff 사이의 비교일 뿐 독립적으로 입증한 사전 공개 건수가 아니다.

수집 observed_at은 635개 모두 kickoff 이후이며 cutoff_proven은 0이다. availability_proven 경기 0, 검증된 배당 관측 경기 0이다. available_at은 가정된 kickoff-24시간이며 증거가 아니다. 사후 수집물이 당시 내용과 동일했다는 보장, 실제 구매 가능 배당, 사전 동결 예측은 확보하지 못했다. strict 명칭도 경기 전 관측 인증을 뜻하지 않는다.

## 1차 문헌 6편

### 1. Sports forecasting: a comparison of the forecast accuracy of prediction markets, betting odds and tipsters

2009 · Martin Spann, Bernd Skiera. [1차 자료](https://onlinelibrary.wiley.com/doi/10.1002/for.1091) · DOI 10.1002/for.1091

확인 범위: publisher abstract and metadata.

결과: 독일 축구 3시즌 678~837경기 비교에서 시장과 배당이 픽스터보다 정확했다. 결합은 정확도를 개선했지만 당시 25% 비용 조건에서 체계적 수익은 없었다.

한계: 2000년대 독일 단일 축구시장; 비용과 배당 환경이 다르며 두 경기 조합 검증이 아니다. 온라인 공개 2008년, 권호 2009년.

적용 제안: 시장 기준선을 유지하고 픽스터의 추가 정보만 검증한다. 정확도 개선을 수익 개선으로 대체하지 않는다.

### 2. The wisdom of amateur crowds: Evidence from an online community of sports tipsters

2019 · Alasdair Brown, J. James Reade. [1차 자료](https://ueaeprints.uea.ac.uk/67529/4/Accepted_manuscript.pdf) · DOI 10.1016/j.ejor.2018.07.015

확인 범위: accepted manuscript abstract, highlights and conclusion; university metadata.

결과: Oddsportal 다수결 전략에서 68,339건 평균 수익률 1.317%를 보고했다. 경험·과거 정확도로 군중을 선별해도 수익이 개선되지 않았다.

한계: 논문 수익률은 본 서비스 수익률이 아니다. 작성자가 앞선 추천을 볼 수 있어 독립 신념과 군중 추종을 분리하지 못한다고 저자들이 명시했다. 2018년 채택본, 2019년 권호.

적용 제안: 과거 적중 상위 작성자만 고르거나 표가 많으면 무조건 낫다고 가정하지 않는다. 전체 군중 대조군과 축소 추정 대조군을 시간 순서로 비교한다.

### 3. Combining predictive distributions

2013 · Tilmann Gneiting, Roopesh Ranjan. [1차 자료](https://arxiv.org/abs/1106.1638) · DOI 10.1214/13-EJS823

확인 범위: author preprint abstract; sections 3.1 and 4; author institutional publication metadata.

결과: 선형 확률 풀은 구성 예측이 보정되어 있어도 보정을 보존한다고 일반적으로 말할 수 없다. 조건에 따라 과분산을 만들며 비선형 풀과 재보정을 검토한다.

한계: 이론과 금융·기상 사례이며 스포츠 수익 실험이 아니다. 표 비율은 처음부터 보정된 확률이 아니므로 정리를 그대로 적용하면 안 된다. arXiv 2011년과 학술지 2013년을 구분한다.

적용 제안: 90:10 혼합과 투표 비율의 확률 보정을 별도로 검사하고, 표 비율을 곧바로 EV에 넣지 않는다.

### 4. Stacked Regressions

1996 · Leo Breiman. [1차 자료](https://statistics.berkeley.edu/sites/default/files/tech-reports/367.pdf) · DOI 10.1023/A:1018046112532

확인 범위: author-hosted paper abstract and publisher metadata.

결과: 교차검증 예측과 비음수 제약 최소제곱으로 여러 회귀 예측을 결합한다. 훈련 자료에서 맞춘 값 대신 외부 예측으로 결합 계수를 구한다.

한계: 회귀와 제곱오차 연구이며 스포츠 확률·수익 최적화의 증거는 아니다. 기술보고서 등록 1992년과 최종 출판 1996년을 구분한다.

적용 제안: 시장 예측을 기준으로 표의 추가 신호를 시간 순서 외부 예측에서만 추정한다. 본 자료에서는 고차원 작성자별 계수를 피한다.

### 5. Using Stacking to Average Bayesian Predictive Distributions (with Discussion)

2018 · Yuling Yao, Aki Vehtari, Daniel Simpson, Andrew Gelman. [1차 자료](https://arxiv.org/abs/1704.02030) · DOI 10.1214/17-BA1091

확인 범위: author preprint abstract and university publisher-PDF metadata.

결과: 후보 중 참 모형이 없을 수 있는 상황에서 proper scoring rule로 예측분포 결합을 최적화한다. LOO와 안정화 기법을 사용한다.

한계: 베이지안 예측분포 논문이며 투표율이나 ROI를 직접 검증하지 않았다. 시계열에 무작위 LOO를 복제하면 미래 누수가 생길 수 있다.

적용 제안: 실서비스 적용 후보는 시간 순서 검증의 log loss와 Brier로 좁힌다. 시장 잔차 stacking은 적용 제안이지 논문이 직접 입증한 스포츠 전략이 아니다.

### 6. How social influence can undermine the wisdom of crowd effect

2011 · Jan Lorenz, Heiko Rauhut, Frank Schweitzer, Dirk Helbing. [1차 자료](https://pmc.ncbi.nlm.nih.gov/articles/PMC3107299/) · DOI 10.1073/pnas.1008636108

확인 범위: full primary article abstract and experimental findings.

결과: 144명 사실 추정 실험에서 타인의 답을 보는 조건은 다양성을 줄이고 정확도 개선 없이 확신을 높일 수 있었다.

한계: 사실 수치 추정 실험으로 스포츠 추천 복제나 모든 사회적 영향의 해로움을 입증하지 않는다.

적용 제안: 독립 작성자·독립 문서·사전 관측 수를 구분하고 동일 문서를 여러 표로 세지 않는다. 단순 일치율만으로 복제를 판정하지 않는다.

## 실행 가능한 권고와 우선순위

1. 먼저 provenance를 보강한다. event_id·시장·방향·작성자 라벨뿐 아니라 원문 URL/해시, 발행 주장, 최초 관측, 수정 관측, 실제 경기 시작, 같은 시점 배당을 따로 저장한다. 문서·사이트·작성자 세 층의 군집을 보존해 독립 표 수와 원시 추천 수를 구분한다. 동일 문서 내 복수 라벨의 처리는 사전에 정하고 원본 집계와 민감도 비교를 남긴다.
2. 신뢰도는 소표본을 축소해 추정하는 후보로 둔다. 과거 적중률 상위자만 남기는 규칙을 전제하지 않는다. 작성자·사이트·리그 계층별 정보는 훈련 기간 안에서만 갱신하고, 표가 하나이거나 신규 작성자면 시장/전체 사전값으로 강하게 축소한다. 독립성이나 과거 신뢰도가 없으면 표 수만으로 확신을 키우지 않는다. 이는 본 자료에서 효과를 검증한 결과가 아니라 후속 실험 설계다.
3. 부모가 담당하는 empirical Bayes, 보정, 검증 혼합 실험과 중복 학습하지 않는다. 이후 추가 후보는 시장 확률을 offset으로 고정하고 투표-시장 차이와 독립 문서 수를 작은 정규화 계수로 반영하는 residual stacking이다. 이진 예시는 logit(p)=logit(p_market)+beta*r_vote이며 다항 경기는 softmax 형태로 처리한다. beta=0 시장 기준선을 포함한다. 이 식은 문헌 원식이 아닌 연구 제안이며 여기서는 적합하지 않았다.
4. 결합 계수·축소 강도·동률 처리·문서 중복 정책은 훈련/검증 안에서 선택하고 마지막 시간 구간은 한 번 평가한다. 같은 경기와 같은 추천 문서가 시계열 경계를 넘어 정보 누수를 만들지 않도록 묶는다. 무작위 행 단위 분할이나 사후 게시 자료를 실전 가용 데이터처럼 다루지 않는다.
5. 독립적 표 수가 부족하면 복잡한 작성자별 가중치보다 단순 시장 기준선과 작은 수의 결합 후보가 현실적이다. 17개 동률을 임의의 첫 방향으로 깨지 않는다. 미리 정한 기권 규칙 또는 시장 기준 처리의 민감도를 부모 실험에서 구분한다.
6. 확률 정확도·보정이 좋아졌다는 결과와 정산 후 수익은 별개다. 이 문헌들은 현재 서비스의 조건부 2폴 수익성을 증명하지 않는다. 2폴 후보가 부족하면 채우지 않는 조건을 유지하고, 결합 확률의 독립성도 표 수로 보장하지 않는다. 연구 손실 제한 OFF는 운영 손실 제한 해제를 뜻하지 않는다.

## 재현과 검증

`python .local-test/TIPSTER/audit.py`로 입력 gzip을 읽고 이벤트·작성자·해시·시각을 집계했다. 시간대 없는 게시 값은 비교에서 제외했다. 고유 경기 597개, 원천 추천 635개, 투표 수 히스토그램 합 597개를 assertion으로 검사했다. Python의 naive/aware datetime 비교 오류를 발견한 뒤 명시적 시간대 값만 비교하도록 수정하고 성공 실행했다. 원본 파일 SHA256은 cae323dd093c7ff9d257d7eb3dc9f296703dd07b592e63b1b59a3dc414fae965이며 manifest와 같다.

산출물은 notes.md, sources.json, feature-audit.json 및 재현용 audit.py다. 데이터가 던져 주는 승패를 대상으로 적중률·ROI·가중치 적합을 수행하지 않았다. 문헌의 효과는 본 서비스의 효과로 전환해 주장하지 않았다. 추적 파일, 운영 코드, 커밋, PR, 배포는 변경하지 않았다.
