---
name: test-dataset
description: 編集可能な LLaDAR 段階手法と提出契約。
---

このファイルが実行手法を定義します。AUTHORING.md はツールと変更のガイドで、代替例は追加の実行指示ではありません。必要な提出がホストに受理されて完了です。文章の回答だけでは完了しません。新しい説明は実行時の --lang に従い、ツール名、フィールド、コード、引用原文は保持します。

# 出典に基づく質問生成

## この Skill の役割と変更箇所

この Skill は知識ソースから、検証可能な質問・期待回答・出典証拠を作成します。問題の種類を変えるときは、下の生成ルールを編集してください。`AUTHORING.md` は編集手順と詳細なツール契約を説明する参考資料です。

指定された出典を読み、要求に応じて直接 QA、意味グラフ、または両方を選択します。出典の事実、注記、根拠のある導出基準を使用し、事実、推論概念、合成制御を区別します。質問は単独で回答可能、期待回答は出典で裏付けられる必要があります。

自由回答は read_source から開始し、割り当て範囲 assigned_start から assigned_end を正確に読み、next_start に従います。submit_question に question、expected_answer、source_refs、answer_basis、rationale、一意な request_id をまとめて提出。answer_basis は source_answer、source_annotation、derived_criterion。status と issues を確認。拒否時は revise_question に item_id、現在の revision、新しい request_id、完全な修正版を渡し、修正は最大三回。結果不明時は get_question_request で元の request_id を確認。同じ内容にだけ ID を再利用します。

互換知識点手法は submit_knowledge_points に statement、topic、read_id と原文 quote を含む evidence を提出し、list_knowledge_points／read_knowledge_point で受理済み点を確認。submit_qa は knowledge_point_id、question、expected_answer を使用。自由回答に id、actual_response、question_type を追加しません。選択・順位形式は要求の typed contract に従います。

概念や比較にグラフが必要なら、証拠付き nodes、edges、facts を submit_semantic_graph に提出し read_semantic_graph で確認して submit_test_plans。direct_fact は値と単位、concept_mapping は完全な候補集合を回答にします。controlled_invariance は一組で一つの宣言済み control_dimension の control_value のみを変え、課題、概念、候補、回答契約、質問骨格を保持。次元 ID、ラベル、意味範囲、値関係、互換次元を宣言し、関係不明の値は使用しません。

ホストが検証、次元選択、重複排除、件数制限、完全なペアと保存を担当。validation_errors と existing_results から修正し、完了済みグラフや計画は再提出しません。Python は探索・計算・修正に使用できます。内蔵自由回答は原子的提出ツールを使い、直列化はホストが担当。互換の candidate_path を使用する場合、そのファイルが全候補の正しいスナップショットで candidate_contract に従います。証拠と契約が有効なら完了です。
