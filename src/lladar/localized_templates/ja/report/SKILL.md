---
name: report
description: 編集可能な LLaDAR 段階手法と提出契約。
---

このファイルが実行手法を定義します。AUTHORING.md はツールと変更のガイドで、代替例は追加の実行指示ではありません。必要な提出がホストに受理されて完了です。文章の回答だけでは完了しません。新しい説明は実行時の --lang に従い、ツール名、フィールド、コード、引用原文は保持します。

# 証拠概要

## この Skill の役割と変更箇所

保存された評価事実を人間向けのレポートにまとめます。読者、文体、順序、強調点を変える場合はここを編集してください。数値と表はホストが生成し、Skill は与えられた証拠を解釈します。`AUTHORING.md` に編集手順があります。

提供された凍結評価事実だけから overview、findings、limitations を作成。数値と表はホストが生成し、文章に数字文字は含めず、測定を再計算・変更・作成しません。証拠に応じて実行エラー、判定エラー、小標本、未実施の人手確認に言及します。

単一質問報告は submit_section(section, text, source_refs, request_id) で三節を提出し、提供された事実キー（summary、aggregates、plan 等）か試行 ID を引用。追加証拠は read_report_fact(reference, start_char) で next_start に従い完全に読む。全主張の根拠を確認し再開時は completed_sections を保持。無効な節だけ revise_section(item_id, request_id, revision, text, source_refs) で修正し、不明な結果は get_section で確認。必要な全節の受理で完了。

状況報告は submit_report(narrative) に空でない overview、findings、limitations の三文字列だけを提出。

stage=report_review は凍結事実と節を比較し報告を書き直しません。必要なら read_report_fact で試行証拠を読み、submit_section_review に各節の {section, approved: boolean, reason} を提出。根拠のない主張、隠されたエラー、小標本の過度な一般化、未証明の人手確認完了を拒否。拒否節のみ作成者に戻し、承認節は保持します。
