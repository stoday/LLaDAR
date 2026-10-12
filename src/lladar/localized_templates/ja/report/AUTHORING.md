# 作成者ガイド

実行指示は SKILL.md にあります。このガイドは参照用で別の Skill として登録しません。代替例を採用する場合は元の規則を置換します。このガイドだけの変更は実行を変更しません。ツール名、フィールド、コード契約は保持します。

下の手法と完全な例を参考に選択、評価、説明の規則を変更。ホストは検証、ファイル、対象 Agent 要求、確定的統計を引き続き担当し、Skill の変更でこの境界は変わりません。

## 手法と提出契約

# 証拠概要

提供された凍結評価事実だけから overview、findings、limitations を作成。数値と表はホストが生成し、文章に数字文字は含めず、測定を再計算・変更・作成しません。証拠に応じて実行エラー、判定エラー、小標本、未実施の人手確認に言及します。

単一質問報告は submit_section(section, text, source_refs, request_id) で三節を提出し、提供された事実キー（summary、aggregates、plan 等）か試行 ID を引用。追加証拠は read_report_fact(reference, start_char) で next_start に従い完全に読む。全主張の根拠を確認し再開時は completed_sections を保持。無効な節だけ revise_section(item_id, request_id, revision, text, source_refs) で修正し、不明な結果は get_section で確認。必要な全節の受理で完了。

状況報告は submit_report(narrative) に空でない overview、findings、limitations の三文字列だけを提出。

stage=report_review は凍結事実と節を比較し報告を書き直しません。必要なら read_report_fact で試行証拠を読み、submit_section_review に各節の {section, approved: boolean, reason} を提出。根拠のない主張、隠されたエラー、小標本の過度な一般化、未証明の人手確認完了を拒否。拒否節のみ作成者に戻し、承認節は保持します。

## 変更例（作成者向けの参照）

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the response outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```
