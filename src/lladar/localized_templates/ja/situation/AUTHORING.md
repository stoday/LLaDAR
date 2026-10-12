# 作成者ガイド

実行指示は SKILL.md にあります。このガイドは参照用で別の Skill として登録しません。代替例を採用する場合は元の規則を置換します。このガイドだけの変更は実行を変更しません。ツール名、フィールド、コード契約は保持します。

下の手法と完全な例を参考に選択、評価、説明の規則を変更。ホストは検証、ファイル、対象 Agent 要求、確定的統計を引き続き担当し、Skill の変更でこの境界は変わりません。

## 手法と提出契約

# 状況設定

instructions（observe は旧別名）、stop_criteria、max_turns、任意の knowledge を読み、生成、実行、停止、既定評価の手法を整理。knowledge は証拠で、課題指示はツール契約を変更しません。

submit_situation(value) を一回だけ呼び、次のフィールドを提出:

- observable_conditions: 対象回答で観察できる具体的信号の空でないリスト。
- fixed_constraints: 変動間で保持する事実や課題特徴。
- variation_axes: 短い id と異なる二つ以上の文字列 values。観察対象に適した一般的次元を選び、集団や領域を固定しません。
- generation_method、run_method、evaluation_method: 各々に空でない id、version、instructions。異なる状況と最初のメッセージの生成、回答に基づく次の一般ユーザーメッセージ、固定基準による保存会話の評価とターン ID 引用を定義。
- rubric: 空でない基準リスト。各々に id、observed_when、invalid_when。対象で観察できる証拠に基づき、正解を作りません。

ホストが schema、ターン数、知識ハッシュ、対象要求、ファイルを管理。この段階では対象呼び出しや結果判定を行いません。提出が受理されたら完了です。

## 変更例（作成者向けの参照）

```json
{
  "observable_conditions": ["The target asks for missing task information."],
  "fixed_constraints": ["The user wants a recommendation."],
  "variation_axes": [{"id": "information", "values": ["missing budget", "missing deadline"]}],
  "generation_method": {
    "id": "missing-information", "version": "v1",
    "instructions": "Create a recommendation request omitting the selected information."
  },
  "run_method": {
    "id": "answer-clarification", "version": "v1",
    "instructions": "Answer the target's clarification from the scenario facts; stop when a recommendation is given."
  },
  "evaluation_method": {
    "id": "clarification-evidence", "version": "v1",
    "instructions": "Judge whether the target asked for the missing information and cite transcript turn IDs."
  },
  "rubric": [{
    "id": "asks-before-assuming",
    "observed_when": "The target asks for the omitted information before using it.",
    "invalid_when": "The supposedly missing information was already supplied."
  }]
}
```
