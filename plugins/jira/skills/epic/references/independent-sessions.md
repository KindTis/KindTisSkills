# 독립 세션 연결

네이티브 독립 세션 기능이 없을 때 사용하는 Codex App Server 연결 지침이다. 준비된 배정 입력·저장소·작업 공간을 전달하고 실제 세션·실행·결과를 확인한다. 업무 내용은 epic의 배정 입력을 따르며 별도 업무나 권한을 추가하지 않는다.

## 기능과 연결

현재 설치된 CLI 도움말과 생성 스키마에서 기능을 확인한다. 아래 명칭은 참고이며 현재 스키마의 실제 입력·응답을 사용한다.

- codex app-server --help
- codex app-server daemon --help
- codex app-server proxy --help
- codex app-server generate-json-schema --out <이번 확인용 임시 디렉터리>

| 목적 | 메서드 | 확인할 값 |
|---|---|---|
| 독립 세션 생성 | thread/start | cwd, ephemeral: false, 반환 thread.id와 실제 cwd |
| 입력과 실행 | turn/start | threadId, input, 반환 turn.id·status |
| 결과 조회 | thread/read | threadId, 필요 시 includeTurns |
| 재연결 | thread/resume | 기존 threadId |

새 세션에서도 필요한 도구·인증·선택된 지침이 사용 가능한지 확인한다. 현재 연결이 자동 복제된다고 가정하지 않는다. 누락된 기능을 알리고 확보한 명세 등 전달 가능한 자료는 입력에 포함한다.

연결마다 initialize 응답 후 initialized를 보낸다. 기존 daemon이 있으면 재사용하고 proxy 등 현재 지원하는 지속 연결을 선택한다. 별도 stdio 서버를 쓴다면 작업 종료 전에 서버를 종료하지 않는다. 연결 종료 후 실행 유지가 불명확하면 연결과 결과 수신을 유지한다.

Windows에서는 창·포커스를 가져오지 않는 백그라운드 프로세스를 사용한다. 기존 사용자 서버를 중지·재시작하거나 원격 접근·인증·승인·샌드박스·hook 설정을 바꾸지 않는다.

## 생성·입력·관측

1. 검증한 작업 공간으로 thread/start를 호출한다. 모델·추론 설정은 사용자 지시·적용 규칙을 따른다. 업무 지시를 baseInstructions나 developerInstructions로 주입해 기본 지침을 교체하지 않는다.
2. 반환 thread.id와 실제 cwd를 확인한다. parentThreadId와 forkedFromId가 없는 최상위 신규 세션인지 확인하며 source/threadSource만으로 독립성을 판단하지 않는다.
3. 이슈 Key·선택된 지침·승인 범위·명세·선행 결과가 담긴 배정 입력을 첫 사용자 입력으로 turn/start에 전달한다. 원래 사용자 제약의 출처를 보존한다.
4. turn/start 응답·이벤트·thread/read로 실행과 결과를 확인한다. 생성·입력 전송 성공만으로 실제 실행이나 완료를 판단하지 않는다. 요청 ID·thread ID·turn ID로 응답을 구분한다.

## 재개와 실패

Key→thread ID 대응과 성공한 결과를 재사용한다. 응답이 불명확하면 기존 세션·turn 상태를 먼저 조회하고 중복 실행을 보내지 않는다. thread/read는 조회이며 실행 가능한 재연결은 thread/resume으로 한다. 후속 입력은 같은 세션에 보낸다.

조회 불가는 미확인으로 반환한다. 오류·연결 종료만으로 새 세션을 만들지 않는다. 단순 연결 확인을 업무 완료, Desktop 표시 또는 연결 종료 후 지속 실행의 검증으로 보고하지 않는다.

구체적 입력은 현재 로컬 스키마를 우선한다. 공식 개념은 [Codex App Server](https://learn.chatgpt.com/docs/app-server)를 참고한다.
