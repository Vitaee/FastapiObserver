from scripts.compare_overhead import compare_samples


def test_stable_measurements_accept_small_changes():
    result = compare_samples([50, 51, 49, 50, 50, 49, 51], [51, 52, 50, 51, 51, 50, 52])
    assert result["passed"] is True


def test_consistent_cpu_regression_fails():
    result = compare_samples([50] * 9, [60] * 9)
    assert result["passed"] is False
    assert result["regression_us"] == 10
    assert result["budget_us"] == 5


def test_noisy_baseline_fails_instead_of_hiding_a_regression():
    result = compare_samples([20, 40, 60, 80, 100, 120, 140], [50] * 7)
    assert result["passed"] is False
    assert result["reliable"] is False


def test_noisy_candidate_cannot_inflate_its_own_budget():
    result = compare_samples([50] * 7, [20, 40, 60, 80, 100, 120, 140])
    assert result["passed"] is False
    assert result["reliable"] is False
