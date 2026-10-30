---
name: run-agent
description: 編集可能な LLaDAR 段階手法と提出契約。
---

このファイルが実行手法を定義します。AUTHORING.md はツールと変更のガイドで、代替例は追加の実行指示ではありません。必要な提出がホストに受理されて完了です。文章の回答だけでは完了しません。新しい説明は実行時の --lang に従い、ツール名、フィールド、コード、引用原文は保持します。

# 安定性実行

## この Skill の役割と変更箇所

データセットのどのケースを実行するか、各ケースを何回繰り返すかを決めます。変更する場合は、下の選択ルールと `repeats` を編集してください。対象 Agent の実行と回答保存はホストが担当します。`AUTHORING.md` に全件・抽出・範囲指定の例があります。

read_dataset() で確認後、write_strategy(content) に完全な Python ソースを提出。入口は select_cases(cases, schedule)。元の各事例を一回だけ repeats=3 で登録します。フィルターや対象 Agent 呼び出しは行わず、戦略以外のファイルは書きません。

cases は不変オブジェクトの tuple で record_index（一始まり）、question、expected_answer を持ちます。case.question を使用。schedule(case, repeats=1) は元の事例と正の整数を受け取り None を返します。再登録は回数を加算し、入口の戻り値は無視します。

len、min、max、range、enumerate、list、tuple、シード付き random.sample、random.choice、random.randint を import なしで使用。ホストが検証し戦略を実行してから対象を呼びます。AUTHORING.md の代替例は変更用で、ここにある有効な規則を実行します。

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```
