# 구현 완료 보고서 생성·배포 운영

코드, 스킬, hook, 문서의 기준은 이 패키지입니다. PC별 값은 `~/.codex/goal-report/settings.json` 한 곳에 둡니다. 자격증명은 기존 OS keyring에 유지합니다. 전역 설정은 패키지 등록과 호환 launcher만 포함합니다.

자동 Cloudflare 업로드 대상은 **실제 goal에 연결되고 개발 작업과 필요한 검증 완료 후 명시적으로 최종화한 구현 완료 보고서만**입니다. 사용자가 현재 보고서의 실제 게시를 직접 지시한 경우에는 아래의 명시 게시 절차로 그 지시와 대상에 한정해 처리합니다. 명세서·구현 계획서·독립 검증 보고서·조사 문서 등은 작성 또는 goal이 완료돼도 대상이 아닙니다. 구현 완료 보고서에 해당 구현의 검증 결과를 포함할 수 있습니다. 문서 작성만 요청받으면 요청한 로컬 문서나 지정된 문서 저장소를 사용하며 이 패키지의 게시 절차를 실행하지 않습니다.

## 정상 흐름과 상태

goal 없는 구현 완료 보고서는 사용자의 요청 → 구현 결과와 근거 선택 → `prepare-report` → 생성 HTML 확인 → 로컬 Markdown 링크 답변 순서입니다. 새 goal이나 ID를 만들지 않습니다. 진행 중 goal의 중간·초안도 로컬 전용입니다.

```powershell
$settings = Get-Content -Raw "$env:USERPROFILE/.codex/goal-report/settings.json" | ConvertFrom-Json
& $settings.python -X utf8 "$env:USERPROFILE/.codex/goal-report/reportctl.py" prepare-report --manifest './session-report.json'
```

메인 구현 goal 완료 절차는 개발 작업과 필요한 검증 완료 → `completion_ready=true` 명시 → `prepare-completion` → 배포 성공 또는 실패를 담은 답변 확인 → goal 완료 → 검사한 답변 전송 순서입니다. 명세·계획·검증·조사 goal의 완료는 배포 준비나 보고서 링크를 요구하지 않습니다. `submit/publish/check-final`은 미리보기·진단·복구 명령입니다.

```powershell
$settings = Get-Content -Raw "$env:USERPROFILE/.codex/goal-report/settings.json" | ConvertFrom-Json
& $settings.python -X utf8 "$env:USERPROFILE/.codex/goal-report/reportctl.py" prepare-completion --manifest './goal-report.json' --draft './answer-draft.md' --output './answer-final.md'
```

- `verified`: 현재 보고서 key(연결된 goal이 있으면 thread/goal도 포함), 고정한 게시 후보 해시, Worker 버전 100% 적용과 현재 보고서의 비로그인 차단을 확인했습니다. 기존 보고서 URL은 순회하지 않습니다. 반환된 URL만 사용합니다. 인증 후 화면·영상 재생 E2E까지 의미하지 않습니다.
- `failed`: 입력·생성·배포·검증 실패를 답변에 기록합니다. 개발 goal은 완료할 수 있습니다. `deployment_status=failed`/`deployed=false`는 이번 시도에서 원격 적용 전 실패, `applied_unverified`/`true`는 적용 확인 후 검사 실패, `unknown`/`null`은 원격 적용 여부 미확인입니다. 시간 초과에는 해당 후보의 마지막 단계 기록을 사용하고 확인할 수 없으면 unknown으로 남깁니다.
- `excluded`: 유형·goal 연결·완료 준비 중 하나가 자격을 충족하지 않아 이번 호출에서 게시하지 않았습니다. 배포 실패가 아니며 URL을 반환하지 않습니다. 유효한 입력은 로컬 미리보기로 유지하고, 결과에는 로컬 Markdown 링크를 반환하며 배포 실패 문구를 붙이지 않습니다. 기존 원격 문서의 삭제나 이전 업로드가 없었다는 의미는 아닙니다.
- `content_status=not-run`: 별도 Access 열람 인증이 없어 인증 후 본문 검사를 실행하지 않았습니다. 완료 답변에도 미실행을 표시합니다. `protection_scope=unresolved`이면 현재 경로의 차단과 별개로 실제 Access 정책 전체 범위가 미확정입니다.
- `answer_check=passed`: 출력 답변이 현재 기록과 일치합니다. 검사 후 답변 변경은 재검사해야 합니다.
- 프로세스 자체의 읽기/쓰기 오류, 실제 ID 누락, 비활성/손상 설정은 종료 코드 1이 될 수 있습니다. 파일이 생성되지 않았다면 보고서 실패를 직접 밝히고 개발 완료와 구분합니다.

`prepare-report`는 보고서를 로컬 생성하며 성공 또는 대상 제외 시 0, 실패 시 1을 반환합니다. 실제 goal 필드를 담은 중간 보고서도 로컬로 생성하며 완료 준비 기록을 만들지 않습니다. `prepare-completion`은 배포 실패를 처리하고 유효한 실패 답변까지 생성한 경우 종료 코드 0을 반환합니다. 대상 제외도 0이며 성공 여부는 `report_status`로 판단합니다. `publish/retry`는 대상 제외 시 0, 배포 실패 시 1, `check-final`은 실패 시 2입니다. 어떤 명령도 Telegram을 보내거나 goal 상태를 변경하지 않습니다.

## 사용자가 직접 요청한 게시

사용자가 현재 보고서와 포함 자산의 실제 게시를 명시적으로 요청했다면 그 지시에 한정한 명시 게시 경로를 사용합니다. 자동 완료 보고서는 기존의 실제 goal·완료 준비 조건을 유지합니다. goal 없는 일반 보고서 생성은 계속 로컬 전용이며, 명시 게시를 위해 새 goal이나 가짜 신원을 만들지 않습니다.

`publish-requested --manifest <입력> --request <사용자 지시 파일>`은 별도 사용자 지시 파일을 받아 현재 보고서·공용 의존 자산·배포 대상·입력·기준 게시 원장·후보 바이트와 연결된 비공개 승인 기록을 준비하고 기존 게시 파이프라인으로 처리합니다. manifest의 임의 승인 필드나 기록의 존재만으로 일반 명령의 게시 자격이 생기지 않습니다. 입력과 대상이 바뀌면 재준비하며, 같은 명시 명령의 복구도 해당 후보에 한정합니다. 다른 pending 후보를 이번 지시로 원격 조회하거나 게시하지 않습니다.

## 공용 리디바탕

개별 보고서와 두 단계 목록의 제목·본문에는 리디바탕을, 소제목과 보조 정보에는 기존 산세리프를 사용합니다. 온라인 보고서와 목록은 같은 사이트의 `fonts/ridibatang/v1/`에 있는 원본 폰트와 라이선스를 참조합니다. 새 보고서마다 같은 공용 URL을 사용하며, 파일은 누적 게시 자산에 보존합니다. 버전 경로의 기존 바이트가 다르면 덮어쓰지 않습니다.

로컬·오프라인 보고서는 CSS의 `local()`로 시스템에 설치된 리디바탕을 사용하고 폰트·라이선스 파일을 동봉하거나 폰트를 다운로드하지 않습니다. 설치된 리디바탕을 사용할 수 없으면 기존 명조 대체 글꼴을 사용합니다. 같은 로컬 보고서를 다시 생성할 때는 해당 보고서에 이전 생성기가 동봉한 것으로 확인한 폰트·라이선스만 제거하며, 다른 보고서와 사용자가 추가한 파일은 보존합니다.

보고서의 캐시 저장 금지와 접근 제한은 유지하고 공용 폰트 경로만 브라우저 캐시를 허용합니다. 현재 보고서가 참조하는 공용 폰트·라이선스는 변경 여부와 관계없이 보호·콘텐츠 검사 대상이며, 원본과 공용 출력 파일 모두 준비·검증 기록에 연결합니다. 폰트의 배포 목록 포함, 실제 파일 업로드, 브라우저 다운로드는 구분해 보고합니다.

## 입력과 근거

[일반 manifest 예시](examples/standalone-report-manifest.json)의 schema_version=1을 사용합니다. `project` slug, 제목·결론·검증 결과가 필요하며 `kind`는 implementation/game/research/verification입니다. `thread_id`는 선택 메타데이터입니다. `goal_id`, `goal_created_at_ms`, `report_id`는 필요하지 않습니다.

`report_type`은 문서의 목적이며 `kind`는 내용 형식입니다. 자동 업로드는 `report_type=implementation_completion`, `kind=implementation` 또는 `game`, 실제 goal 신원, 유효한 완료 준비 봉인을 모두 충족해야 합니다. 명시 게시도 같은 구현 완료 보고서 유형·내용 형식을 요구하며 별도 사용자 지시와 게시 승인 봉인을 검증합니다. 다른 유형은 `specification`, `implementation_plan`, `verification_report`, `research_report`, `other`입니다. 유형 누락은 `other`로 처리하고 업로드하지 않습니다. `implementation_completion`과 연구·검증 형식의 조합은 잘못된 입력으로 거부합니다. 제목의 "완료"나 goal 완료 상태만으로 업로드 자격을 추정하지 않습니다.

기존 유형 없는 입력은 자동 승격하지 않습니다. 실제 구현 완료 보고서임을 확인한 입력만 해당 유형을 명시하여 재제출합니다. `prepare-report`, `prepare-completion`, `publish/retry` 및 직접 배포 adapter에 같은 제한을 적용합니다. 제외된 입력은 성공 캐시나 미확정 게시 후보가 있어도 인증·원격 검사·업로드를 시작하지 않습니다. 이미 게시된 과거 자산은 보존합니다.

일반 보고서 ID는 저장소에서 자동 생성하고 `report-sources/<입력 파일 경로 해시>.json`에 보관합니다. 같은 입력 파일 경로는 같은 보고서를 갱신·복구하며, 새 보고서에는 새 입력 파일을 사용합니다. 입력 원본은 수정하지 않습니다. 같은 입력 파일의 project 변경은 기존 주소를 혼동하지 않도록 거부합니다. 실행 결과에 key가 반환되므로 필요하면 복구 명령에서 사용합니다.

goal 완료 경로만 [goal manifest 예시](examples/report-manifest.json)의 실제 `thread_id`, `goal_id`와 생성 시각을 요구합니다. 기존 goal 보고서의 key·URL은 유지합니다. 현재 작업에 goal이 없으면 검증용으로 가짜 ID나 goal을 만들지 않습니다.

goal 완료 표시 전에 로컬 goal DB에서 정확한 thread/goal ID와 생성 시각을 확인합니다. `completion_ready=true`는 생산자가 개발 작업과 필요한 검증을 완료했다는 선언이며, `prepare-completion`만 `identity.json` 및 입력·미디어·구현·게시 대상 fingerprint에 연결된 `readiness.json`을 생성합니다. DB 신원을 확인하지 못하면 게시하지 않습니다. manifest의 임의 readiness 필드나 ID/생성 시각만으로 게시할 수 없습니다. tests의 passed/failed/not-run은 그대로 보존합니다. 입력·근거·대상이 변경되면 다시 최종화해야 합니다. 준비 시 확인한 신원을 보관하므로 goal 완료로 DB 행이 사라진 뒤 같은 보고서를 복구할 수 있습니다.

tests는 passed/failed/not-run 및 조건·근거·미실행 사유를 포함합니다. research/verification은 조건·출처가 있는 비교표가 필수이고 차트는 의미 있는 수치 비교에만 사용합니다. game은 execution 역할의 실제 이미지/영상이 필요하며 illustration은 실행 근거를 대신하지 않습니다.

본문은 완료 결과와 사용자에게 달라진 동작, 확인한 흐름, 중요한 실패·제약·미확인 사항을 중심으로 간결하게 작성합니다. SHA·커밋 해시·내부 ID·긴 절대 경로와 상세 명령·로그는 내부 추적 기록에 남기고 본문에서는 제외합니다. 결과를 판단하는 데 필요한 조건과 확인 방법은 짧은 설명으로 항상 표시하며 접기·펼치기 기능을 사용하지 않습니다. `sections[].log_path`에서 읽은 `log_text`는 내부 manifest에 보관하고 공개 HTML에는 넣지 않습니다. `sections[].text`에는 독자에게 필요한 설명만 작성합니다.

새 보고서와 전체 프로젝트·프로젝트별 보고서 목록은 배경 `#FAF9F5`와 기본 글자 `#141413`을 운영체제 테마와 관계없이 유지하는 에디토리얼 형식입니다. 목록에는 전용 `catalogue-theme.css`를 사용하고 새 보고서에는 보고서별 스타일 파일을 사용합니다. 기존 개별 보고서가 참조하는 공유 `theme.css`와 기존 본문·미디어·주소는 유지하며, 기존 게시 보고서를 이 형식으로 일괄 재작성하지 않습니다.

전체 프로젝트 목록은 이름순이며, 프로젝트 안의 보고서 목록은 저장된 `publication_order`의 내림차순입니다. 새 보고서에만 게시 후보 생성 시 기존 최대 순번 다음 값을 배정하고, 게시 성공 시 같은 순번을 원장에 보관합니다. 기존 보고서의 재검증·재배포·입력 재제출·누적 묶음 포함은 순번을 바꾸지 않습니다. 로컬 생성·온라인 후보·로컬 목록 재구성이 같은 발행 메타데이터를 사용하며, 입력 manifest의 임의 값으로 발행 이력을 만들지 않습니다.

발행 정보가 없던 과거 자료는 확인 가능한 게시 기록을 근거로 한 번 순서를 정해 저장합니다. 이 순서는 남아 있는 게시 확인 기록에 따른 추정이며 최초 발행 순서의 완전한 복원을 의미하지 않습니다. 발행 근거가 없는 항목과 미발행 로컬 보고서는 하단에서 내부 키 순으로 안정 배치합니다. goal 시작 시각·파일 수정 시각·후보 준비 시각을 과거 발행일로 사용하지 않으며, 내부 순번이나 추적 값은 공개 본문에 표시하지 않습니다.

로컬 미리보기와 목록 재구성에서는 필요한 이전 정보를 메모리에서 해석하며 `public`·`published.json`·`verification`을 변경하지 않습니다. 실제 게시 경로에서 기존 자료의 이전 정보를 한 번 보관하고, 새 보고서의 발행 순번은 성공 확정 시 원장에 반영합니다.

`evidence_root` 내부의 명시된 상대 경로만 읽습니다. 원시 세션, 자격증명, `.env`, 전체 소스는 입력에서 제외합니다. 로그는 민감 내용을 제거한 파생 로그만 선택합니다. 원본을 보존하고 이미지를 합성하여 실행 근거로 표시하지 않습니다.

`report_media.py`는 방향별 최대 1920×1080/1080×1920 안으로만 줄이고 확대하지 않습니다. 미디어는 파일당 24 MiB 이하입니다. 기본 H.264, 원본 이하 30fps이며 움직임 검증에 60fps까지 유지합니다. 오디오는 기본 보존합니다. ffmpeg/ffprobe 경로는 동일 설정 파일에서 관리합니다. 실제 iPhone Safari와 인증된 원격 재생은 별도 검증 항목입니다.

## hook의 책임과 한계

SessionStart는 구현 완료 보고서에만 적용되는 패키지 스킬을 제공합니다. 지원되는 update_goal 도구의 PreToolUse는 준비된 구현 완료 보고서가 있을 때 실제 현재 goal과 준비 기록을 대조합니다. 성공·실패 기록 모두 완료를 허용합니다. PostToolUse는 그 호출이 실제 완료됐는지 확인하고, Stop은 해당 turn에 준비·완료된 구현 goal의 답변만 검사합니다. goal 행이 사라져도 완료 전 캡처한 ID와 완료 응답을 대조합니다. 불일치하면 추정하지 않습니다.

준비 기록이 없으면 구현 완료에만 절차가 필요하다는 안내를 제공하며 모든 goal을 일괄 차단하지 않습니다. hook 입력에는 업무 유형이 없으므로 목표 문구로 구현 여부를 추측하지 않습니다. 문서 goal이나 대상 제외 기록에는 링크·실패 답변 의무를 만들지 않습니다. 구현 goal의 준비 누락을 hook만으로 강제할 수 없으므로 전역 AGENTS와 스킬의 명시적 완료 전 절차가 기준입니다.

누락·다른 goal 링크·오래된 검증·실패 표시 누락은 한 번의 보정 continuation을 요청합니다. 보정 turn에도 해결되지 않으면 경고와 failed 상태를 남깁니다. hook은 배포·Telegram·goal 변경을 하지 않습니다.

첫 답변 표시 전 차단은 보장하지 않습니다. 호스트가 해당 도구를 hook에 전달하지 않거나 hook이 신뢰되지 않았으면 자동 검사가 실행되지 않습니다. 자동 진입이 실사용 환경에서 검증되기 전까지 전역 AGENTS의 짧은 참조를 유지합니다. 새 hook의 신뢰 승인은 `/hooks`에서 정의를 검토한 뒤 수행하며 설치 프로그램이 신뢰 상태를 대신 쓰지 않습니다.

## 보관과 복구

1.4.1은 `published.json`의 확정된 파일 목록과 `published-objects/<sha256>`의 보관 바이트로 독립된 `candidates/<key>/<후보 ID>/public`을 만듭니다. 실패한 후보는 다른 보고서에 합류하지 않습니다. 확정 후 기존 `public` 경로를 갱신합니다. 기존 파일은 같은 내용 해시 보관 파일에 연결하므로 후보마다 전체 미디어를 중복 복사하지 않습니다. 새 보고서와 root/project 목록, `theme.css`, `_headers` 변경을 허용합니다. 기존 개별 HTML은 보관된 원본 SHA와 정확한 back 링크 치환을 대조한 navigation migration만 허용합니다. 다른 기존 본문·미디어 변경이나 누락은 배포기가 거부합니다. 새 공용 목록 및 이전 탐색 경로도 비로그인 보호/인증 후 콘텐츠 검사 대상에 포함합니다.

첫 전환 때 기존 public의 전체 자산 해시가 마지막 성공 기록과 일치해야 자동으로 게시 기준을 만듭니다. 불일치하면 초안까지 임의로 합류시키지 않고 중단합니다. 이후 전체 파일 목록은 기존 보고서 보존과 Cloudflare 버전 구성에 사용하며, HTTP 검사에는 현재 보고서·변경 공용 자산과 대표 보호 경로만 사용합니다. 보호 설정 변경 후 전체 점검은 별도 진단으로 취급합니다.

읽기 요청은 통신 오류 및 HTTP 429/500/502/503/504에 최대 3회 시도하고 Retry-After를 존중합니다. `deployment_timeout_seconds`의 전체 예산(기본 300초, 최대 900초)을 공유하며 업로드 명령을 무작정 재실행하지 않습니다. 안전한 단계·경로·상태·오류 코드만 실시간 stderr와 `deployment-attempts/<key>.json`에 남깁니다. 원시 Wrangler 출력·응답·로그인 URL·인증 값은 기록하지 않습니다.

원격 버전에 동일 자산 해시가 이미 적용됐으면 재업로드 없이 보호·내용 검사를 재개합니다. `pending-deployment.json`은 적용 후 실패 또는 적용 여부 불명인 후보를 보존합니다. 다른 보고서를 게시하기 전에 해당 후보를 원격 상태와 대조합니다. 이미 적용된 후보는 검사 후 확정하며, 게시 기준 버전이 그대로면 미적용 후보를 제외합니다. 양쪽과 다른 버전이면 자동 덮어쓰기를 보류합니다. 기존 성공 기록 재사용도 현재 원격 자산 버전과 현재 보고서 보호를 다시 검사합니다.

원격 게시만 publish.lock으로 직렬화합니다. 입력 저장·미리보기는 짧은 store.lock을 사용하며 네트워크 작업 동안 저장할 수 있습니다. 배포 중 입력이 바뀌면 그 입력에 과거 후보의 성공 기록을 발급하지 않습니다.

인증 후 검사는 별도 테스트 인증을 실행 프로세스에만 제공합니다. 기존 서비스 토큰은 `CODEX_REPORT_ACCESS_CLIENT_ID`와 `CODEX_REPORT_ACCESS_CLIENT_SECRET`, 기존 테스트 세션은 `CODEX_REPORT_ACCESS_COOKIE`(CF_Authorization 값)로 전달합니다. 설정 JSON이나 manifest에 비밀 값을 쓰지 않습니다. 새 인증·정책을 자동 생성하지 않으며 Wrangler OAuth를 열람 인증으로 보내지 않습니다. 인증이 있을 때 변경 파일의 HTTP 200·MIME·바이트 해시와 MP4의 실제 HTTP 206·Content-Range·부분 바이트를 확인합니다. 브라우저 영상 재생 자체는 별도 E2E입니다.

기존 `root`와 `public/projects/<project>/<key>/` 경로를 유지합니다. 입력, 신원, 작업·검증·완료·turn 기록은 public 밖에 둡니다. 누적 자산을 보존하며 자동 삭제하지 않습니다. 전역 notify는 기본 complete/blocked 알림만 보내고 보고서 배포·추가 링크 메시지를 수행하지 않습니다.

입력/환경 문제를 해결하고 동일 manifest·원래 초안으로 `prepare-completion`을 다시 실행합니다. 유효한 검증은 재사용합니다. 수동 배포 복구는 `retry --key <실제 key>`이며 Telegram을 보내지 않습니다. 이전 `--notify`, `--ack-unsent` 옵션은 제거했습니다. 이미 전달된 구형 보고서의 입력 변경은 기존 보호를 유지합니다.

## 설치·전환·롤백

저장소의 `install_goal_report.py`는 패키지를 버전 단위로 설치하고 단일 설정을 이전합니다. 같은 버전의 다른 바이트는 덮어쓰지 않습니다. 수정 배포는 plugin.json 버전을 올립니다. 이어 `codex plugin add goal-report@personal --json`으로 등록합니다. 실행 중인 세션의 새 hook 반영과 신뢰 검토는 별개이며 기존 세션을 임의로 재시작하지 않습니다.

보고서 형식이나 버전만 갱신하고 기존 전역 지침을 보존할 때는 `install_goal_report.py --preserve-instructions`를 사용합니다. 이 옵션은 전역 `AGENTS.md`의 쓰기만 건너뛰며 패키지 스킬·설정·호환 진입점·등록 정보는 정상 갱신합니다. 옵션을 생략한 기존 설치 동작은 유지합니다.

옛 reportctl/스킬/REPORTING 경로는 호환 참조만 남기고 중복 구현은 보관 백업으로 이동합니다. 현재 설정은 settings.json에만 있습니다. `enabled=false`로 보고서 생산·hook을 비활성화해도 일반 goal 알림은 유지됩니다. 되돌릴 때 설치 backup 기록을 사용하며 보고서 저장소와 알림 중복 기록은 삭제하거나 과거 값으로 덮어쓰지 않습니다. Worker 삭제·이름 변경·Access 정책 변경은 하지 않습니다.

## 프로젝트 분류와 로컬 이전

root의 프로젝트명·보고서 수 목록에서 `/projects/<canonical-project>/index.html` 프로젝트 목록으로 이동하고, 개별 페이지는 기존 `/projects/<original-project>/<key>/` 주소를 유지합니다. 개별 `보고서 모음`은 해당 프로젝트, `전체 프로젝트`는 root로 돌아갑니다. 링크는 `index.html`을 포함하여 로컬 file URL에서도 동작합니다.

선택 입력 `project_origin`은 실제 원본 프로젝트/저장소 경로입니다. Git의 common directory가 같은 worktree는 원본 저장소 이름으로 분류하며 Git 없는 폴더는 확인한 폴더 이름을 사용합니다. canonical 경로와 표시명을 입력 저장 시 분리하여 보관합니다. 원본 근거가 없는 기존 slug는 유지하며 임의 alias를 합치지 않습니다.

`public`/`published.json`/게시 원본 보관은 원격 확정 기준입니다. 로컬 전체 목록을 새로 만들 때는 확정 원본을 `previews`로 복사하고 목록·탐색 링크만 갱신합니다. no-goal 작업을 이유로 원격 확정 기준이나 과거 성공 receipt를 덮어쓰지 않습니다. 다음 적격 goal 게시 후보에서 nav 이전을 포함해 원격 적용할 수 있습니다.

기존 로컬 전체 목록 재구성은 `reportctl.py rebuild-local-catalogue`로 실행합니다. 확정 게시 원본의 해시를 확인하여 previews에 없는 자산만 채우고 nav/index만 재생성합니다. 기존 로컬 미리보기 본문·미디어는 유지하며 목록 제목·결론은 실제 유지되는 페이지를 기준으로 합니다. 제출만 갱신되고 아직 렌더되지 않은 입력은 목록 내용을 바꾸지 않습니다. public, published.json, verification은 유지하며 인증이나 원격 요청이 없습니다. 기존 local-only 보고서도 함께 분류합니다. `goal_created_at_ms`는 예시의 고정 숫자를 사용하지 않고 실제 DB에서 확보합니다. 선택 project_origin은 확인 가능한 실제 경로가 있을 때만 입력합니다.

1.4.1의 `rebuild-local-catalogue` CLI는 기존 `store.lock`으로 전체 로컬 재구성을 직렬화하여 다른 세션의 입력 저장·렌더와 겹치지 않습니다. 1.4.0 등 기존 설치 버전은 덮어쓰지 않습니다.

1.4.2는 기존 `class="back"` 링크와 보관 샘플의 정확한 `<a href="...">← 보고서 모음</a>` 탐색 요소에만 href 이전을 적용합니다. 오래된 안전한 프로젝트/하위 경로의 비표준 key도 같은 원본 SHA·href-only 보존 검사를 적용합니다. 신규 보고서 경로와 event key의 24자리 검증은 유지합니다.
