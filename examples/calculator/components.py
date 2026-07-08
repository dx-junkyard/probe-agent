"""Calculator components for the probe-agent example."""

def add(a: float, b: float) -> float:
    return a + b

def add_v2(a: float, b: float) -> float:
    # ログ出力や型チェックなどの改善が加えられた新しいバージョン
    return float(a) + float(b)

def subtract(a: float, b: float) -> float:
    return a - b

def subtract_v2(a: float, b: float) -> float:
    return float(a) - float(b)

def multiply(a: float, b: float) -> float:
    return a * b

def multiply_v2(a: float, b: float) -> float:
    return float(a) * float(b)

def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Division by zero")
    return a / b

def divide_v2(a: float, b: float) -> float:
    if float(b) == 0.0:
        # v2では安全に infinity を返す、もしくは詳細なエラーメッセージを出すなどの設計変更
        return float('inf') if a >= 0 else float('-inf')
    return float(a) / float(b)
