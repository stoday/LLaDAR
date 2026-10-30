# 作成者ガイド

## ここから始める

`SKILL.md` のケース選択と繰り返し回数を編集します。対象 Agent の実行はホストが行います。

```sh
lladar run-agent DATASET.jsonl --project PROJECT --skill ./my-run-agent --output responses.jsonl
```

実行指示は SKILL.md にあります。このガイドは参照用で別の Skill として登録しません。代替例を採用する場合は元の規則を置換します。このガイドだけの変更は実行を変更しません。ツール名、フィールド、コード契約は保持します。

下の手法と完全な例を参考に選択、評価、説明の規則を変更。ホストは検証、ファイル、対象 Agent 要求、確定的統計を引き続き担当し、Skill の変更でこの境界は変わりません。

## 手法と提出契約

# 安定性実行

read_dataset() で確認後、write_strategy(content) に完全な Python ソースを提出。入口は select_cases(cases, schedule)。元の各事例を一回だけ repeats=3 で登録します。フィルターや対象 Agent 呼び出しは行わず、戦略以外のファイルは書きません。

cases は不変オブジェクトの tuple で record_index（一始まり）、question、expected_answer を持ちます。case.question を使用。schedule(case, repeats=1) は元の事例と正の整数を受け取り None を返します。再登録は回数を加算し、入口の戻り値は無視します。

len、min、max、range、enumerate、list、tuple、シード付き random.sample、random.choice、random.randint を import なしで使用。ホストが検証し戦略を実行してから対象を呼びます。AUTHORING.md の代替例は変更用で、ここにある有効な規則を実行します。

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

## 変更例（作成者向けの参照）

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

```python
def select_cases(cases, schedule):
    for case in random.sample(cases, min(5, len(cases))):
        schedule(case, repeats=1)
```

```python
def select_cases(cases, schedule):
    for case in cases:
        if case.record_index <= 5:
            schedule(case, repeats=2)
```
