# egd-sample-leave

EGD（Example-Gated Development／具体例ゲート型AI駆動開発）を実際に回して見せるための公開サンプルです。題材は有給休暇申請です。

> 人間は正しさを決める。AIは実現方法を決める。成果は証拠で判断する。

## このリポジトリで見られるもの

- `.egd/cycles/<ID>/contract.yaml`：人間がGate 1で承認した具体例（契約）
- `tests/acceptance/<ID>/`：具体例から作られ、ロックされた受入テスト
- タグ `egd/<ID>/v<N>`：ロックした時点の契約と受入テスト
- Pull Requestの `egd-verify`：ロックした基準で成果物を判定するCI
- `.egd/cycles/<ID>/report.md`：人間がGate 2で読む受入レポート

## 動かし方

```bash
pip install -r requirements.txt
PYTHONPATH=src python3 -m leave.web      # http://127.0.0.1:8000
python3 -m pytest tests/unit -q
python3 .egd/bin/egd.py status
```

AIエージェントは `AGENTS.md` の手順に従います。どのエージェントでも使えます。
