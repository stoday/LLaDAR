---
name: eval
description: 編集可能な LLaDAR 段階手法と提出契約。
---

このファイルが実行手法を定義します。AUTHORING.md はツールと変更のガイドで、代替例は追加の実行指示ではありません。必要な提出がホストに受理されて完了です。文章の回答だけでは完了しません。新しい説明は実行時の --lang に従い、ツール名、フィールド、コード、引用原文は保持します。

# 回答判定

stage=plan で submit_plan に boolean の correct 次元を持つ計画を提出。stage=judgment は凍結計画を使い expected_answer と actual_response だけを比較し、submit_judgment に values と空でない reason を提出。根拠があれば correct=true、不一致は false、判定不能は null と理由。集計は計算しません。

status を確認し、形式拒否時は revise_plan／revise_judgment に item_id、新 request_id、現在の revision と完全な修正版を提出。修正は最大三回。get_plan／get_judgment で不明な結果を確認。受理済み計画は変更不可。指定 trial_id と plan_version を使用し request_id は項目単位。actual_response=null で response_read があれば長い回答です。read_trial_response(trial_id, start_char) と next_start で全体を読み、実行失敗と扱いません。

カスタム Skill の typed、semantic-probe 回答もその凍結計画で評価。提出の受理で完了。

stage=situation_judgment ではこの正解判定手法に状況基準はありません。judgment_contract に従い validity=indeterminate、behavior=indeterminate、evidence_turn_ids=[] を提出し、reason でカスタム Skill に完全な状況基準が必要と説明。plan ツールはありません。基準を追加する場合は実際のターン ID を引用し生成制約を保持します。

```json
{
  "title": "Answer correctness",
  "approach": "Accept equivalent wording while requiring all answer conditions.",
  "dimensions": [
    {"name": "correct", "description": "Matches the expected answer including required conditions.", "kind": "boolean"}
  ],
  "limitations": ["Only the supplied question, expected answer and actual response are evidence."]
}
```
