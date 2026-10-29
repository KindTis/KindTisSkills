# Codex 독립 세션 연결

네이티브 독립 세션 기능이 없을 때 사용하는 Codex App Server 연결 방법이다. 실행 조건과 세션 계약은 `jira:work`를 따른다.

## 실행 전 확인

실제 설치된 `codex`의 도움말과 스키마에서 필요한 기능을 확인한다.

```text
codex app-server --help
codex app-server daemon --help
codex app-server proxy --help
codex app-server generate-json-schema --out <이번 확인용 임시 디렉터리>
```

아래 메서드와 예시는 작성 당시 로컬 CLI 스키마에서 확인했다. 현재 스키마에 맞춰 실제 값과 응답 ID를 사용한다.

| 목적 | 확인한 메서드 | 핵심 입력·결과 |
| --- | --- | --- |
| 새 최상위 세션 | `thread/start` | `cwd`, `ephemeral: false`; 결과 `thread.id`와 실제 `cwd` |
| 작업 입력과 실행 | `turn/start` | 필수 `threadId`, `input`; 결과 `turn.id`, `turn.status` |
| 실제 goal 조회 | `thread/goal/get` | 필수 `threadId`; 결과 `goal` 또는 `null` |
| 실제 goal 등록 | `thread/goal/set` | `threadId`, `objective`, `status: active`; 결과 `goal` |
| 세션·결과 조회 | `thread/read` | `threadId`, 필요 시 `includeTurns`; 결과 `thread` |
| 기존 세션 재연결 | `thread/resume` | 기존 `threadId`; 결과 `thread` |

Jira 도구와 필요한 스킬이 **새 세션에서도** 사용 가능한지 확인한다. 현재 세션의 연결·인증·도구 목록이 자동으로 복제된다고 가정하지 않는다. 누락된 기능은 의존하는 실행 전에 알리고, 현재 세션이 확보한 명세 등 전달 가능한 입력은 업무 지시에 포함한다.

## 연결과 수명

현재 환경에서 사용할 수 있는 지속 연결을 선택한다. `codex app-server daemon start`는 실행 중인 로컬 daemon이 없으면 시작하는 명령이고, `codex app-server proxy`는 해당 daemon의 control socket과 표준 입출력을 연결한다. 별도의 `codex app-server --stdio`를 실행하는 경우에는 작업을 수행하는 서버 프로세스를 작업이 끝나기 전에 종료하지 않는다.

연결마다 `initialize` 응답을 받은 뒤 `initialized`를 보낸다. Windows에서는 창·포커스를 가져오지 않는 백그라운드 프로세스와 표준 입출력을 사용한다. 기존 사용자 서버를 재시작·중지하거나 원격 접근·인증 설정을 바꾸지 않는다. 승인·샌드박스·hook 규칙을 끄는 옵션을 추가하지 않는다.

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"jira_work","version":"1.0.0"}}}
```

```json
{"method":"initialized","params":{}}
```

연결 종료 후 실행 유지가 보장되는지 확인하고, 불명확하면 연결과 결과 수신을 유지한다. 여러 세션의 응답은 요청 ID·thread ID·turn ID로 구분한다.

## 맥락을 전달한 뒤 실제 goal 실행 확인

1. 지시한 작업 공간, 없으면 검증된 저장소를 `cwd`로 새 세션을 시작한다. 모델·추론 설정은 사용자 지정과 적용 규칙에 맞게 현재 스키마로 전달하고 반환된 설정을 확인한다. 기본 지침을 교체하는 `baseInstructions`나 `developerInstructions`로 업무 지시를 주입하지 않는다.

```json
{"id":2,"method":"thread/start","params":{"cwd":"<검증한 절대 경로>","ephemeral":false}}
```

반환된 `thread.id`를 사용한다. `parentThreadId`와 `forkedFromId`가 없는 최상위 신규 세션인지 확인하고 `sessionId`도 기록한다. 생성 경로를 뜻하는 `source`나 `threadSource`는 부모 관계의 증거로 쓰지 않는다.

2. [업무 지시](session-prompt.md)를 실제 값으로 채워 **첫 사용자 입력**으로 전달한다. 이 입력은 해당 세션이 맥락을 읽은 뒤 실제 goal을 등록하고 작업하도록 지시한다.

```json
{"id":3,"method":"turn/start","params":{"threadId":"<반환된 thread.id>","input":[{"type":"text","text":"<실제 값으로 채운 전체 업무 지시>"}]}}
```

3. `thread/goal/get`으로 objective와 활성 상태를, `turn/start` 응답·이벤트·`thread/read`로 실제 실행과 결과를 확인한다. 빠르게 끝났으면 최종 goal과 결과를 사용한다.

```json
{"id":4,"method":"thread/goal/get","params":{"threadId":"<반환된 thread.id>"}}
```

```json
{"id":5,"method":"thread/read","params":{"threadId":"<반환된 thread.id>","includeTurns":true}}
```

goal이 `null`이면 첫 turn의 진행·실패 여부부터 확인한다. 세션이 직접 goal을 생성할 수 없을 때만 업무 맥락이 전달됐고 기존 goal이 없음을 확인한 뒤 `thread/goal/set`을 사용한다.

```json
{"id":6,"method":"thread/goal/set","params":{"threadId":"<반환된 thread.id>","objective":"첨부된 명세를 $improve:implement 스킬 지침에 따라 작업한다. 작업은 지시한 작업 공간에서 진행하고, 지시가 없으면 현재 브랜치에서 진행한다","status":"active"}}
```

`thread/goal/set`의 활성화가 자동 실행을 유발하는지는 입력 스키마만으로 보장되지 않는다. 맥락 없이 goal부터 활성화하거나, 이미 진행 중인 turn에 같은 실행 지시를 중복 전송하지 않는다. 실제 실행이 시작되지 않았다면 현 상태를 확인하고 기존 세션에서 필요한 시작 단계만 수행한다. 사용자가 요청하지 않은 token budget을 넣지 않는다.

## 재개와 실패

에픽의 Key→thread ID 대응과 실제 세션 결과를 재사용한다. `thread/read`는 조회이며 기존 세션을 실행 가능하게 재연결해야 할 때에는 `thread/resume`을 사용한다. 오류나 연결 종료만으로 새 세션을 만들지 않는다. 활성 goal을 불필요하게 다시 설정하거나 사용자의 다른 goal을 덮어쓰지 않는다.

조회 불가는 미확인으로 보고한다. 실행·완료 판단은 `jira:work`의 확인 기준을 따른다. 이 절차만으로 Desktop 목록 표시나 연결 종료 후 자동 지속 실행을 검증했다고 주장하지 않는다.

공식 개념과 프로토콜 설명: [Codex App Server](https://learn.chatgpt.com/docs/app-server). 구체적인 호출 입력은 현재 설치본이 생성한 스키마를 우선한다.
