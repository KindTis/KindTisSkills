---
name: goal-completion-report
description: 구현 goal을 완료 처리하기 전, 또는 사용자가 구현 완료 보고서의 생성·게시를 요청할 때 사용합니다. 명세·계획·검증·조사 문서는 대상이 아닙니다.
---

# 구현 완료 보고서

PC 설정은 `~/.codex/goal-report/settings.json` 한 곳에 둡니다. `enabled=false`이면 자동 보고서 절차를 적용하지 않습니다. 사용자가 승인한 구현 결과와 근거만 선택하고 실제 실행·검증과 미검증 경계를 구분합니다.

## 사람이 읽는 본문

형식·목록·정렬은 런타임이 처리하므로 manifest 내용만 실제 결과로 작성합니다.

- 결론에는 무엇을 완료했고 이제 무엇이 가능한지 먼저 씁니다. 변경 목록은 사용자에게 달라진 동작을 중심으로 작성하고 같은 설명을 반복하지 않습니다.
- 검증에는 확인한 사용자 흐름, 필요한 실행 조건과 실제 결과를 짧게 씁니다. 실패·미실행·중요한 제약과 미확인 사항은 본문에 그대로 표시합니다. 상세 명령이나 로그로 설명을 대신하지 않습니다.
- SHA·커밋 해시·내부 thread/goal ID·긴 절대 경로·상세 실행 로그는 본문에서 제외합니다. 필요한 문서나 산출물은 의미 있는 이름으로 안내하고, 추적 값과 원래 검증 근거는 내부 기록에 보관합니다.
- `sections`에는 독자가 결과를 판단하는 데 필요한 설명만 넣습니다. 선택한 파생 로그의 `log_path`/`log_text`는 내부 근거로 보관하며 공개 HTML에는 표시하지 않습니다. 필요한 확인 방법은 짧은 본문으로 작성해 항상 보이게 합니다.

자동 goal 완료 절차에서는 실제 goal에 연결된 `report_type=implementation_completion` 및 `kind=implementation` 또는 `game` 보고서만 Cloudflare에 게시합니다. 개발 작업과 필요한 검증이 끝난 뒤 **명시적으로 `prepare-completion`으로 최종화한 보고서**만 대상입니다. ID, 제목의 완료 표현, 입력의 `completion_ready`만으로 자격을 판단하지 않습니다. 명세·계획·독립 검증·조사 문서는 goal이 있어도 로컬 문서로 보관합니다. 사용자가 현재 보고서의 게시를 직접 지시한 경우는 아래의 별도 명시 게시 절차를 따릅니다.

## Goal 없는 작업과 진행 중 보고서

새 goal을 만들거나 ID를 추정하지 않습니다. [로컬 manifest 예시](../../examples/standalone-report-manifest.json)를 실제 결과로 작성하고 설정의 `python`으로 `~/.codex/goal-report/reportctl.py prepare-report --manifest <입력>`을 실행합니다. 별도의 명시 게시 요청이 없는 goal 없는 구현 완료 보고서와 아직 최종화하지 않은 goal 보고서는 로컬 HTML과 로컬 Markdown 링크만 반환합니다. 일반 `publish`, `retry`나 직접 adapter로 우회해도 인증·원격 조회·업로드하지 않습니다.

생성 HTML과 `report_status=excluded`, `local_generated=true`, `deployed=false`를 확인하고 반환된 **로컬 보고서 링크**를 답변에 포함합니다. 게시 대상 제외는 배포 실패가 아닙니다. 과거 성공 URL을 현재 결과로 답하지 않습니다. 로컬 생성만 필요하면 `submit --manifest <입력>`도 사용할 수 있습니다. 이 명령은 `report_status` 없이 `html`과 `deployed=false`를 반환하며, 근거 파일이 없거나 goal 신원이 준비되지 않은 goal 보고서는 종료 코드 2로 알립니다.

## 사용자가 직접 요청한 게시

사용자가 현재 보고서의 실제 게시를 명시적으로 지시한 경우에는 그 지시에 한정한 명시 게시 경로를 사용할 수 있습니다. 이는 자동 goal 완료 절차의 게시 조건을 바꾸지 않습니다. 요청하지 않은 게시를 추정하거나, 새 goal·가짜 ID·manifest의 승인 필드로 자격을 만들지 않습니다.

실제 구현 결과와 필요한 검증을 마친 보고서 입력 및 사용자 지시 원문을 별도 UTF-8 파일로 작성합니다. 설정의 `python`으로 `~/.codex/goal-report/reportctl.py publish-requested --manifest <입력> --request <사용자 지시 파일>`을 실행합니다. 명시 게시 승인은 현재 보고서·공용 의존 자산·배포 대상·입력과 게시 후보에 묶인 비공개 기록으로 검증합니다. 일반 `prepare-report`, `publish`, `retry`는 이 기록을 자동으로 게시 자격에 사용하지 않습니다.

기존 보고서·접근 제한·배포 검증을 유지하고 실제 반환 상태를 확인합니다. 같은 명시 명령으로 해당 후보를 복구할 수 있으며 다른 pending 후보를 이번 승인으로 처리하지 않습니다. 검증된 현재 URL만 안내하고, 인증 후 내용·화면 검사를 수행하지 못했다면 미검증 범위를 명시합니다.

## 구현 goal 완료 준비

개발 작업과 필요한 검증을 완료한 후 [goal manifest 예시](../../examples/report-manifest.json)에 실제 thread ID·goal ID를 기록합니다. 생성 시각은 런타임이 goal DB에서 확인하므로 넣지 않습니다. `completion_ready=true`는 생산자가 구현 작업과 필요한 검증을 끝냈다는 명시적 선언입니다. 실제 goal DB의 정확한 신원을 확인하고 입력·미디어·구현·게시 대상 fingerprint에 준비 기록을 봉인합니다. 검증 `failed`/`not-run`은 이유와 완료 기준 충족 여부를 사실대로 적고, 모든 검증을 passed로 바꾸지 않습니다.

1. 실제 변경·검증·미검증 경계를 담은 답변 초안을 UTF-8 BOM 없이 작성합니다.
2. 설정의 `python`으로 `~/.codex/goal-report/reportctl.py prepare-completion --manifest <입력> --draft <초안> --output <최종답변>`을 실행합니다. 출력은 입력과 다른 경로를 사용합니다.
3. 실제 신원, 생성 HTML, `answer_check=passed`와 출력 답변을 읽습니다. `verified`는 검증된 URL을, `failed`는 원인과 로컬 생성 상태를 담습니다. **보고서 장애는 개발 goal 완료를 막지 않습니다.** 단, `verified` 기록의 산출물이 준비 후 바뀌었으면 완료가 거부되므로 `prepare-completion`을 다시 실행합니다. CLI 자체 실패는 직접 명시하고 미검증 URL을 제시하지 않습니다.
4. 개발 완료 조건이 충족되면 goal을 완료하고 검사한 답변을 전송합니다. 도구 수치 반영 등 답변이 변경되면 `check-final --key <현재 key> --thread-id <실제 ID> --goal-id <실제 ID> --draft <답변>`으로 다시 검사합니다.

보고서 finalization은 개발 검증 완료 후, goal 완료 상태 표시 직전에 수행합니다. 진행 중 goal의 중간·초안을 게시하려고 이 절차를 사용하지 않습니다. 실제 신원 확인이 안 되면 추정하거나 가짜 goal을 만들지 않습니다. goal 완료 전 복구는 같은 입력과 원래 초안으로 `prepare-completion`을 다시 실행합니다. `retry`는 완료 기록을 갱신하지 않으므로 이 복구에 쓰지 않습니다. goal 완료 후에는 `prepare-completion`을 다시 실행하지 않고, 반환된 key의 `retry`로 배포만 복구합니다. 입력이 바뀌면 이전 준비 기록은 무효이며 다시 최종화합니다.

## 프로젝트 목록과 근거

목록은 전체 프로젝트 → 프로젝트별 보고서 → 개별 보고서 순서입니다. 새 보고서는 작업 제목이 아니라 원본 프로젝트명으로 `project` slug를 정하고, 원본 경로를 확인할 수 있으면 `project_origin`을 함께 기록합니다. `project`는 기존 개별 URL을 보존하는 slug입니다. 가능하면 `project_origin`에 실제 원본 프로젝트/저장소 경로를 적습니다. Git common directory가 같은 worktree는 원본 저장소 프로젝트로 분류합니다. 원본 근거가 없는 이름을 임의로 병합하지 않습니다. 기존 보고서는 본문·미디어·URL을 유지하고 탐색 링크만 제한적으로 이전합니다.

게임은 실제 실행 이미지/영상이 필요합니다. 원시 세션·자격증명·전체 소스는 게시하지 않고 선택한 미디어와 민감 내용을 제거한 파생 로그만 사용합니다. `content_status=not-run`과 `protection_scope=unresolved`는 각각 인증 후 본문·영상 검사 미실행과 Access 전체 범위 미확정으로 답변에 표시합니다. Telegram을 직접 호출하지 않습니다. hook은 보조 검사이며 첫 답변 전 차단을 보장하지 않습니다. 상세 입력·복구·설치는 [REPORTING.md](../../REPORTING.md)를 읽습니다.
