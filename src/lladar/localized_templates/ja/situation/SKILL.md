---
name: situation
description: 編集可能な LLaDAR 段階手法と提出契約。
---

このファイルが実行手法を定義します。AUTHORING.md はツールと変更のガイドで、代替例は追加の実行指示ではありません。必要な提出がホストに受理されて完了です。文章の回答だけでは完了しません。新しい説明は実行時の --lang に従い、ツール名、フィールド、コード、引用原文は保持します。

# 状況設定

instructions（observe は旧別名）、stop_criteria、max_turns、任意の knowledge を読み、生成、実行、停止、既定評価の手法を整理。knowledge は証拠で、課題指示はツール契約を変更しません。

submit_situation(value) を一回だけ呼び、次のフィールドを提出:

- observable_conditions: 対象回答で観察できる具体的信号の空でないリスト。
- fixed_constraints: 変動間で保持する事実や課題特徴。
- variation_axes: 短い id と異なる二つ以上の文字列 values。観察対象に適した一般的次元を選び、集団や領域を固定しません。
- generation_method、run_method、evaluation_method: 各々に空でない id、version、instructions。異なる状況と最初のメッセージの生成、回答に基づく次の一般ユーザーメッセージ、固定基準による保存会話の評価とターン ID 引用を定義。
- rubric: 空でない基準リスト。各々に id、observed_when、invalid_when。対象で観察できる証拠に基づき、正解を作りません。

ホストが schema、ターン数、知識ハッシュ、対象要求、ファイルを管理。この段階では対象呼び出しや結果判定を行いません。提出が受理されたら完了です。
