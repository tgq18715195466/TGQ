def test_metrics_measured_on_real_predictions():
    from app.evaluation import retrieval_metrics, character_accuracy, percentile
    result = retrieval_metrics(['b', 'a'], ['a'], 10)
    assert result['recall_at_10'] == 1
    assert result['mrr_at_10'] == .5
    assert result['context_precision'] == .5
    assert character_accuracy('合同123', '合同124') == .8
    assert percentile([1, 2, 3, 4, 5], 95) == 5


def test_empty_ground_truth_not_perfect_retrieval():
    from app.evaluation import retrieval_metrics
    assert retrieval_metrics([], [], 10)['recall_at_10'] is None


def test_fixture_evaluation_reports_no_fabricated_ragas_scores():
    from app.evaluation import evaluate_prediction
    case = {'question_type': 'unanswerable', 'evidence_chunk_ids': [], 'expected_answer': ''}
    result = evaluate_prediction(case, {'retrieved_chunk_ids': [], 'refused': True, 'answer': ''})
    assert result['abstention_correct'] == 1
    assert result['faithfulness'] is None
