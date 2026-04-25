from core.evaluator import evaluate_result
from unittest.mock import MagicMock

def test_evaluator():
    prompt = "Find price of Eggs"
    
    # Case 1: Success
    res_success = "The price of Eggs is $3.99"
    history = MagicMock()
    history.is_successful.return_value = True
    history.has_errors.return_value = False
    assert evaluate_result(prompt, res_success, history) == True
    print("Test Case 1 (Success) passed.")

    # Case 2: Failure (No numbers)
    res_no_num = "Found eggs but no price listed"
    assert evaluate_result(prompt, res_no_num, history) == False
    print("Test Case 2 (No numbers) passed.")

    # Case 3: Failure (Keyword mismatch)
    res_wrong = "Found Bread for $2.00"
    assert evaluate_result(prompt, res_wrong, history) == False
    print("Test Case 3 (Keyword mismatch) passed.")

    # Case 4: Failure (Failure keyword)
    res_fail = "I failed to find the price of eggs"
    assert evaluate_result(prompt, res_fail, history) == False
    print("Test Case 4 (Failure keyword) passed.")

if __name__ == "__main__":
    test_evaluator()
