# KindTisSkills

개인 Codex 전역 지침과 플러그인 소스를 Git으로 추적·관리하고, 필요한 플러그인을 선택 설치하는 마켓플레이스입니다.

## 구성

| 경로 | 내용 | 버전 |
| --- | --- | --- |
| [.agents/plugins/marketplace.json](.agents/plugins/marketplace.json) | `kindtis-skills` 마켓플레이스와 선택 설치 목록 | — |
| [AGENTS.md](AGENTS.md) | 전역 작업 지침 전체 사본 | 2026-10-29 기준 |
| [agents](agents) | Codex 커스텀 역할 설정(Advisor·Art Director·Implementer·Tester·Watchdog) | — |
| [archive](archive) | 플러그인에서 분리한 과거 기록(설치 대상 아님) | — |
| [plugins/goal-report](plugins/goal-report) | 구현 완료 보고서 스킬, 실행 코드, hook, 예시, 글꼴 | 1.4.9 |
| [plugins/improve](plugins/improve) | 명세 검토·개선 및 확정 명세 구현 스킬, 참조 문서, 테스트 자료 | 1.1.2 |
| [plugins/jira](plugins/jira) | Jira 에픽·명세·개발·상태 관리 스킬과 참조 문서 | 1.3.1 |

## 마켓플레이스 등록과 설치

이 구성이 GitHub의 `main` 브랜치에 반영된 후 다음 명령으로 마켓플레이스를 등록합니다.

```powershell
codex plugin marketplace add https://github.com/KindTis/KindTisSkills.git --ref main
codex plugin list --marketplace kindtis-skills --available --json
```

목록에서 원하는 플러그인을 고른 뒤 해당 설치 명령을 실행합니다. 마켓플레이스를 등록하는 것만으로 세 플러그인이 모두 설치되지는 않습니다.

```powershell
codex plugin add goal-report@kindtis-skills
codex plugin add improve@kindtis-skills
codex plugin add jira@kindtis-skills
```

jira는 improve 플러그인과 superpowers 플러그인의 `brainstorming` 스킬이 필요하며, worktree 작업을 지시하면 `using-git-worktrees`도 사용합니다. improve는 설치되어 있으면 superpowers의 `writing-plans`·`executing-plans`·`using-git-worktrees`와 ponytail의 `ponytail-review`를 보조로 사용합니다.

데스크톱 앱에서는 등록 후 앱을 다시 시작하고 플러그인 디렉터리에서 **KindTisSkills** 마켓플레이스를 선택해 원하는 플러그인을 설치합니다.

GitHub에 반영하기 전에는 이 저장소 루트에서 `codex plugin marketplace add .`로 로컬 구성을 확인할 수 있습니다. 로컬 등록과 GitHub 등록은 같은 마켓플레이스 이름을 사용하므로, GitHub 원본으로 전환할 때는 `codex plugin marketplace remove kindtis-skills`로 로컬 등록을 제거한 뒤 위의 GitHub 등록 명령을 실행합니다.

전역 `AGENTS.md`와 [agents](agents)의 역할 설정 적용은 별도로 관리합니다. goal-report의 PC별 실행·배포 설정은 [REPORTING.md](plugins/goal-report/REPORTING.md)를 참고합니다. Jira 작업에는 사용 환경에서 제공하는 Jira 도구와 해당 서비스 접근 권한이 필요합니다.

## 플러그인 추가와 갱신

새 플러그인은 `plugins/<이름>/` 아래에 패키지를 넣고, `marketplace.json`의 `plugins` 배열에 이름과 `./plugins/<이름>` 상대 경로를 등록합니다. `source.path`는 저장소 루트를 기준으로 해석합니다. 각 항목에는 선택 설치 정책(`AVAILABLE`), 사용 시 인증 정책(`ON_USE`), 분류(`category`)를 함께 지정합니다.

기존 플러그인을 변경할 때는 해당 디렉터리와 `plugin.json` 버전을 함께 갱신해 GitHub에 반영합니다. Git 원본의 마켓플레이스는 다음 명령으로 갱신할 수 있습니다.

```powershell
codex plugin marketplace upgrade kindtis-skills
```

이 명령은 마켓플레이스 원본을 갱신합니다. 설치된 플러그인까지 자동 갱신하는 구성은 포함하지 않으며, 설치본에 새 버전을 적용하는 동작은 별도로 확인해야 합니다.

마켓플레이스 형식과 명령은 [OpenAI 플러그인 문서](https://developers.openai.com/plugins/build/plugins#marketplace-metadata)와 [CLI 문서](https://learn.chatgpt.com/docs/developer-commands#codex-plugin-marketplace)를 참고합니다.

## 원본과 갱신

2026-10-29에 다음 로컬 원본에서 복사했습니다. 세 플러그인은 같은 버전의 설치 캐시와 파일 내용이 일치하는 것을 확인했습니다.

- `AGENTS.md`: `~/.codex/AGENTS.md`
- `plugins/goal-report/`: `~/.codex/plugins/goal-report/1.4.8/`
- `plugins/improve/`: `~/.codex/plugins/improve/`
- `plugins/jira/`: `~/.codex/plugins/jira/`

각 플러그인의 `plugin.json`과 내부 디렉터리 구조를 유지했습니다. 이후 갱신할 때는 해당 디렉터리의 변경과 `plugin.json` 버전을 함께 검토합니다.

최초 복사 원본과 이 저장소는 자동으로 동기화되지 않습니다. 이후 이 저장소에서 관리하는 플러그인은 위 마켓플레이스에서 선택 설치하고, PC별 실행 환경은 별도로 관리합니다.

Python 캐시, 과거 버전 디렉터리, PC별 설정, 인증 정보, 실제 보고서와 실행 기록은 복사 대상에서 제외했습니다. goal-report에 포함된 글꼴의 이용 조건은 [글꼴 라이선스](plugins/goal-report/runtime/fonts/RIDIBatang-LICENSE.html)를 따릅니다.
