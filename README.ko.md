# unifi-iptv-igmp-keeper

[English](README.md) | [한국어](README.ko.md)

저장소: [aroxu/unifi-btv](https://github.com/aroxu/unifi-btv)

UniFi OS / UCG 계열 게이트웨이에서 IPv4 multicast IPTV의 IGMP membership을
유지하는 adaptive daemon입니다. Python 3.8 이상과 표준 라이브러리만 사용하며,
MIT 라이선스로 배포합니다. 저장소 이름은 `unifi-btv`이지만 프로그램은 B tv에
고정된 인터페이스나 셋톱박스 MAC을 사용하지 않습니다.

**현재 상태: 초기 구현과 로컬 검증 완료, 실제 게이트웨이 검증 대기.**
기존 고정 설정 workaround는 SK Broadband B tv 환경에서 동작했습니다.
새 adaptive 버전은 실제 UCG와 여러 UniFi 펌웨어에서 추가 확인이 필요합니다.
[기존 B tv 실험 기록](docs/skb-btv.md)과 [검증 범위](docs/validation.md)를 참고하세요.

**0.1.0에서 몇 분 뒤 재생이 멈추고 `source-filter/SSM evidence present`가 표시되면
0.1.1로 업데이트하세요.** 로컬 제어용 multicast 구독을 SSM으로 오분류하여 갱신을
중단하던 문제를 수정했습니다. 실제 source-filter와 SSM 구독에 대한 보호는 유지합니다.

기존 IGMP proxy와 multicast routing 구성이 필요합니다. IPTV VLAN, DHCP,
방화벽이나 multicast route를 자동으로 설정하는 도구는 아닙니다.
IGMPv3 source filtering과 IPv6 MLD는 지원하지 않습니다.

## 1라인 설치 / 업데이트

아래 요구사항을 준비한 뒤 **게이트웨이 호스트의 root SSH 세션**에서 실행합니다.

```sh
curl -fsSL https://raw.githubusercontent.com/aroxu/unifi-btv/main/install.sh | bash
```

`main` 브랜치의 소스 압축 파일을 한 번에 받아 필요한 파일과 설정을 확인한 뒤
설치하고 서비스를 시작합니다. 기존 `/data/iptv-igmp-keeper/config.ini`는 유지하므로,
업데이트할 때도 같은 명령을 실행하면 됩니다. 요구사항 확인이나 다운로드가
실패하면 기존 설치 파일을 교체하기 전에 중단합니다. 임시 다운로드 파일은
성공·실패 시 정리합니다. `main`은 최신 코드를 가리킵니다.

설치 전에 소스만 받아 확인하려면 다음 명령을 사용합니다. root 권한이 필요하지
않으며 서비스나 게이트웨이 설정을 변경하지 않습니다.

```sh
curl -fsSL https://raw.githubusercontent.com/aroxu/unifi-btv/main/install.sh | bash -s -- --download-only ./unifi-btv-source
```

대상 디렉터리는 아직 존재하지 않아야 합니다. 받은 디렉터리에서 아래 dry-run을
진행한 뒤 `sudo sh install.sh`로 로컬 설치할 수 있습니다.

## 요구사항

- UniFi OS **호스트**에서 실행합니다. 컨테이너 내부 실행은 지원 대상이 아닙니다.
- IPv4 multicast routing, `/proc`, `/sys`, AF_PACKET과 classic BPF를 지원하는 Linux.
- `/usr/bin/python3` 3.8 이상, `systemctl`, `systemd-run`, POSIX shell과 `install`.
- 1라인 설치에는 `curl`, `tar`, `mktemp`, `bash`가 추가로 필요합니다.
- 선택한 upstream과 downstream 인터페이스 각각에 IPv4 주소가 있어야 합니다.
- [unifi-utilities/unifi-common](https://github.com/unifi-utilities/unifi-common)의
  `udm-boot.service`가 활성화되어 `/data/on_boot.d` 스크립트를 실행해야 합니다.

on-boot framework는 해당 저장소의 설치 안내에 따라 먼저 설치하고 확인하세요.
이 프로젝트의 설치 스크립트는 framework를 설치하지 않습니다.
`/data/on_boot.d` 디렉터리만 만든다고 부팅 시 실행되는 것은 아닙니다.
펌웨어 업데이트 후에는 Python과 on-boot 동작을 다시 확인하세요.

## 동작 방식

1. `/proc/net/ip_mr_vif`, `/proc/net/ip_mr_cache`에서 multicast 경로를 읽습니다.
   입력 인터페이스가 하나로 확정될 때 upstream을 선택하고, 해당 경로의 출력
   인터페이스에서 downstream을 탐지합니다. 설정 파일로 직접 지정할 수도 있습니다.
2. 커널 패킷 필터로 IGMP만 수집합니다. downstream 구독을 인터페이스·클라이언트
   MAC·그룹별로 추적하므로 한 셋톱박스의 Leave가 다른 셋톱박스의 구독을 지우지 않습니다.
3. 기본 `auto` 모드는 최근 upstream IGMPv2 Query가 관측되어야 보정을 시작합니다.
   해당 upstream의 `force_igmp_version=2`를 적용하고 IGMPv2 Membership Report를
   주기적으로 보냅니다. 기존 값은 복원할 수 있도록 기록합니다.
4. 최근 클라이언트 Report, 대응하는 multicast route, 최근 증가한 패킷 카운터가
   모두 확인된 ASM 그룹만 갱신합니다. `224.0.0.0/24`, SSM 범위인 `232.0.0.0/8`은
   갱신하지 않으며 upstream Leave를 만들어 보내지 않습니다.
5. v2 보정이 허용된 상태에서 경로와 트래픽이 확인된 ASM 그룹의 클라이언트 Report가
   150초 동안 없으면 General Query를 fallback으로 보냅니다. 외부 Query의 응답 대기
   시간은 존중하지만, 응답이 없으면 재시도합니다. 다른 채널의 Report로 인해 누락된
   그룹의 확인이 미뤄지지 않습니다. 인터페이스별로 빈도를 제한하며 설정에서 끌 수 있습니다.
6. 5초마다 재탐지합니다. UniFi reprovision으로 인터페이스가 재생성되거나 주소·MAC·경로가
   바뀌면 수집 소켓과 관측 정보를 초기화하고 다시 학습합니다.

## IGMPv3 / SSM 보호와 동작 모드

| `force_version` | 동작 |
| --- | --- |
| `auto` | 최근 upstream v2 Query가 관측된 경우에만 보정합니다. 기본값입니다. |
| `v2` | v2 ASM 제공자임을 확인한 사용자의 명시적 설정입니다. Query 대기 없이 시작할 수 있습니다. |
| `off` | 관측만 합니다. Membership Report, General Query, 버전 강제를 수행하지 않습니다. |

**`auto`와 `v2` 모두 upstream v1/v3 Query 또는 downstream source-filter/SSM
증거가 관측되면 보정을 중단하고 자신이 변경한 버전 설정을 복원합니다.**
버전 강제는 인터페이스 전체에 영향을 주므로 혼합 서비스가 관측된 인터페이스도
보정하지 않습니다. IGMPv3의 `EXCLUDE {}`는 ASM 구독으로 추적할 수 있지만,
source 주소를 포함한 record와 비어 있지 않은 ALLOW/BLOCK 변경은 보정을 차단합니다.
빈 ALLOW/BLOCK 변경은 무시하며, `224.0.0.0/24`의 로컬 제어용 구독은 SSM으로
분류하지 않습니다.

패킷 관측은 제공자의 기능을 완전히 증명하지 못합니다. 처음에는 여러 Query 주기를
포함하는 dry-run으로 확인하세요. IGMPv3/SSM 제공자는 `off`를 사용하고 원래의
proxy 구성을 점검해야 합니다. 기본 route를 WAN으로 추측하거나 downstream을
일괄 v2로 강제하지 않습니다. 여러 upstream이 탐지되면 명시적 설정 전까지 대기합니다.
Linux 기본 multicast table과 현재 network namespace만 지원하며 VRF·다른 table은
지원하지 않습니다.

## 설치 전 확인 / 수동 설정

소스 디렉터리에서 실행합니다. 실제 IGMP 수집에는 root 권한이 필요합니다.

```sh
python3 src/iptv-igmp-keeper.py --config config/config.example.ini --check-config
python3 src/iptv-igmp-keeper.py --config config/config.example.ini --discover
sudo python3 src/iptv-igmp-keeper.py --config config/config.example.ini --dry-run --once --observe-seconds 450
```

`--discover`는 파일을 읽기만 합니다. `--dry-run`은 패킷·sysctl·로그·상태 파일을
변경하지 않고 판단 결과를 출력합니다. `--once`의 기본 관측 시간은 15초로,
주기적인 upstream Query를 보기에는 짧을 수 있습니다. `--dry-run` 없이 실행하는
`--once`는 실제 보정을 수행할 수 있으며 종료할 때 자신이 변경한 값을 복원합니다.

자동 탐지가 대기 중이면 채널을 켜고 다시 확인하세요. 최초 join 자체가 실패하여
route가 생기지 않는 경우에는 실제 인터페이스 역할을 확인한 뒤 직접 지정합니다.

```ini
[interfaces]
upstream = eth4
downstream = br935
```

위 이름은 예시이며 UniFi 기본값이 아닙니다. 명시적 설정도 실제 route·트래픽·구독
확인 조건을 우회하지 않습니다. 여러 downstream은 쉼표로 구분하며 공백은 넣지 않습니다.
전체 옵션은 [설정 예제](config/config.example.ini)를 참고하세요.

## 설치 파일 / 서비스 관리

로컬 설치는 다음과 같이 실행합니다.

```sh
sudo sh install.sh
```

설치 경로는 `/data/iptv-igmp-keeper`이며 부팅 hook은
`/data/on_boot.d/50-iptv-igmp-keeper.sh`입니다. hook은 부팅마다 transient systemd
서비스를 생성합니다. 이 서비스에 `systemctl enable`을 실행하지 않습니다.
실패 시 재시작하며 SIGTERM으로 종료하면 자신이 변경한 sysctl을 복원합니다.

설정은 `/data/iptv-igmp-keeper/config.ini`에서 변경합니다. 반영하려면:

```sh
systemctl restart iptv-igmp-keeper.service
```

상태 확인:

```sh
python3 /data/iptv-igmp-keeper/keeper.py --config /data/iptv-igmp-keeper/config.ini --status
systemctl status iptv-igmp-keeper.service --no-pager
```

수동 실행에서 설치된 설정을 읽으려면 `--config`를 지정해야 합니다.
생략하면 프로그램의 기본 설정을 사용합니다. 잘못된 설정은 검증 단계에서 실패합니다.

## 갱신 주기 / 만료 / 로그

자동 갱신 주기는 downstream bridge의 `multicast_membership_interval` 중 가장 짧은
값의 1/4이며 **10–60초**로 제한합니다. sysfs clock tick은 `SC_CLK_TCK`로 초 단위로
변환합니다. bridge timer를 읽을 수 없으면 260초를 가정하여 60초마다 갱신합니다.
직접 지정하는 `refresh_interval`은 10–120초입니다. 이 값은 ISP timeout을 측정한
결과가 아니므로 실제 upstream timeout이 더 짧으면 간격을 줄이세요.

클라이언트 구독은 300초, 최근 트래픽 증거는 90초 후 만료합니다. 잠깐 멈춘 스트림은
갱신할 수 있지만 오래 중단된 그룹을 계속 유지하지 않습니다. IGMPv2 Report suppression
때문에 일부 클라이언트가 관측되지 않을 수 있어 구독 추적은 보수적으로 동작합니다.

로그는 `/data/iptv-igmp-keeper/keeper.log`에 기록하며 **512 KiB × 최대 3개**, 약
1.5 MiB로 회전합니다. 상태 변경·fallback Query·오류와 시간당 heartbeat만 남깁니다.
상태 파일은 `/run/iptv-igmp-keeper`에 기록하여 지속 저장소의 쓰기를 줄입니다.
`--status`는 마지막 snapshot과 경과 시간을 보여주며 없거나 오래되면 종료 코드 1을
반환합니다. `reports_sent`와 `fallback_queries_sent`는 시작 후 전송 호출이 성공한
횟수입니다. `last_report_age_seconds`, `memberships`, `source_filter_groups`로
마지막 전송 시점·구독 응답 시간·차단한 그룹을 확인할 수 있습니다. 전송 호출 성공은
상대방의 수신 확인을 의미하지 않으며, 최신 상태 파일만으로 정상 재생을 보장하지 않습니다.

비정상 종료 후에는 같은 부팅의 override journal로 기존 값을 복원합니다.
현재 값과 인터페이스가 자신이 변경한 대상에 해당할 때만 복원합니다.
외부 도구가 동일한 값을 설정한 경우 소유권을 구분할 수 없으므로 여러 버전 관리
도구를 동시에 사용하지 않는 것이 좋습니다. 재부팅하면 `/run`과 커널 override는 사라집니다.

## 삭제

```sh
sudo sh /data/iptv-igmp-keeper/uninstall.sh
# 기본 설정과 로그까지 삭제하려면:
sudo sh /data/iptv-igmp-keeper/uninstall.sh --purge
```

서비스를 중지하고 자신이 변경한 값을 복원한 뒤 실행 파일과 boot hook을 제거합니다.
기본 삭제는 설정·로그·삭제 스크립트를 유지합니다. `--purge`는 알려진 기본 경로의
파일까지 제거합니다. 사용자 지정 로그·상태 경로와 공용 on-boot framework는 유지합니다.

## B tv 사례 / 문제 해결

기존 SK Broadband B tv 실험에서는 upstream이 IGMPv2 Query를 보냈지만 UCG가
IGMPv3 Report를 보내 join 문제가 있었고, `force_igmp_version=2`로 해결했습니다.
약 260초 뒤 membership 갱신 실패로 끊긴 스트림은 WAN에서 수동 IGMPv2 Membership
Report를 보내 즉시 복구되었습니다. 특정 환경의 결과이며 모든 SKB 지역·펌웨어의
동작을 의미하지 않습니다.

같은 환경에서 UniFi **Prioritize QoS**가 multicast jitter를 유발했습니다.
membership이 유지되는데 재생이 흔들리면 IPTV의 해당 기능을 비활성화해 비교해 보세요.
IGMP 변경과 QoS 변경은 하나씩 적용하여 원인을 확인합니다.

추가 자료: [B tv 실험 기록](docs/skb-btv.md), [문제 해결](docs/troubleshooting.md),
[로컬 검증과 게이트웨이 확인 절차](docs/validation.md). 이 상세 문서는 영어로 제공됩니다.

## 개발 / 라이선스

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile src/iptv-igmp-keeper.py
shellcheck install.sh uninstall.sh unifi/50-iptv-igmp-keeper.sh
```

runtime pip 패키지는 필요하지 않습니다. 보고할 때는 기기·UniFi OS 버전, Query 버전,
인터페이스와 route 정보를 포함하되 실제 주소와 설정의 식별 정보는 제거해 주세요.
[MIT 라이선스](LICENSE)의 독립적인 커뮤니티 프로젝트이며 Ubiquiti·ISP와 제휴하지 않습니다.

참고: [Linux IP sysctl](https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html),
[Linux bridge](https://www.kernel.org/doc/html/latest/networking/bridge.html),
[RFC 2236](https://www.rfc-editor.org/rfc/rfc2236.html),
[RFC 3376](https://www.rfc-editor.org/rfc/rfc3376.html).
