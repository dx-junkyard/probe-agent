"""Sample calculator application utilizing @probe for execution tracing and shadow mode.
Supports advanced expression parsing (including parenthesis and multiple operators).

Run:
    $env:PROBE_SERVER_URL="http://localhost:8000"

    python main.py                # automated demo (default)
    python main.py --interactive  # interactive REPL
    python main.py --api          # FastAPI server on port 8080
"""

import sys
import re
from probe_agent import probe, set_candidate
from components import (
    add, add_v2,
    subtract, subtract_v2,
    multiply, multiply_v2,
    divide, divide_v2
)

# 新しい候補(V2)を登録。
# これにより、PROBE_SERVER 側で shadow モードが有効になった際に比較が行われます。
set_candidate("calculator_add", add_v2)
set_candidate("calculator_subtract", subtract_v2)
set_candidate("calculator_multiply", multiply_v2)
set_candidate("calculator_divide", divide_v2)

@probe(component_id="calculator_add")
def run_add(a: float, b: float) -> float:
    return add(a, b)

@probe(component_id="calculator_subtract")
def run_subtract(a: float, b: float) -> float:
    return subtract(a, b)

@probe(component_id="calculator_multiply")
def run_multiply(a: float, b: float) -> float:
    return multiply(a, b)

@probe(component_id="calculator_divide")
def run_divide(a: float, b: float) -> float:
    return divide(a, b)

# --- 数式評価パーサー (Recursive Descent Parser) ---
# 数値（小数対応）または演算子（+, -, *, /, (, )）にマッチするトークナイザー
TOKEN_RE = re.compile(r"(\d*\.\d+|\d+|[\+\-\*/\(\)])")

class ExpressionEvaluator:
    """Evaluates mathematical expressions with operator precedence and parenthesis.
    Calls tracked probe functions (run_add, run_subtract, etc.) for each operation.
    """
    def __init__(self, expression: str):
        # スペースをすべて除去した上で、トークンに分割
        tokens = TOKEN_RE.findall(expression.replace(" ", ""))
        self.tokens = [t for t in tokens if t.strip()]
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, expected=None):
        token = self.peek()
        if token is None:
            raise ValueError("不完全な数式です。")
        if expected and token != expected:
            raise ValueError(f"期待されない演算子です: {token}")
        self.pos += 1
        return token

    def parse(self) -> float:
        if not self.tokens:
            raise ValueError("空の入力です。")
        res = self.expr()
        if self.pos < len(self.tokens):
            raise ValueError(f"無効な文字が残っています: {self.tokens[self.pos:]}")
        return res

    def expr(self) -> float:
        term_val = self.term()
        while self.pos < len(self.tokens) and self.tokens[self.pos] in ('+', '-'):
            op = self.tokens[self.pos]
            self.pos += 1
            next_term = self.term()
            if op == "+":
                term_val = run_add(term_val, next_term)
            else:
                term_val = run_subtract(term_val, next_term)
        return term_val

    def term(self) -> float:
        factor_val = self.factor()
        while self.pos < len(self.tokens) and self.tokens[self.pos] in ('*', '/'):
            op = self.tokens[self.pos]
            self.pos += 1
            next_factor = self.factor()
            if op == "*":
                factor_val = run_multiply(factor_val, next_factor)
            else:
                factor_val = run_divide(factor_val, next_factor)
        return factor_val

    def factor(self) -> float:
        token = self.peek()
        if token == '(':
            self.consume('(')
            res = self.expr()
            self.consume(')')
            return res
        elif token == '-':
            self.consume('-')
            # 単項マイナスの処理
            return -self.factor()
        elif token == '+':
            self.consume('+')
            return self.factor()
        
        # 数値のパース
        token = self.consume()
        try:
            return float(token)
        except ValueError:
            raise ValueError(f"無効な数値です: '{token}'")

def evaluate_expression(expression: str) -> float:
    evaluator = ExpressionEvaluator(expression)
    return evaluator.parse()

def build_app():
    """Build the FastAPI app. Imported lazily so the demo/interactive
    modes don't require fastapi/pydantic/uvicorn to be installed."""
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    class CalculationRequest(BaseModel):
        expression: str

    class CalculationResponse(BaseModel):
        result: float

    app = FastAPI(title="Calculator API", description="API for advanced expression parsing", version="1.0.0")

    @app.post("/calculate", response_model=CalculationResponse)
    def calculate_api(request: CalculationRequest):
        try:
            res = evaluate_expression(request.expression)
            return CalculationResponse(result=res)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ZeroDivisionError:
            raise HTTPException(status_code=400, detail="0で割ることはできません。")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"エラーが発生しました: {e}")

    return app

def run_interactive():
    print("--- 電卓アプリ (Interactive Mode) ---")
    print("複雑な数式（例: 256526-929+6565 や (2+3)*5）を計算できます。")
    print("各ステップの演算は、裏で PROBE の個別トレース（calculator_add 等）として送信されます。")
    print("終了するには 'exit' を入力してください。\n")
    
    while True:
        try:
            user_input = input("式を入力してください -> ").strip()
            if user_input.lower() == "exit":
                break
            if not user_input:
                continue
            
            res = evaluate_expression(user_input)
            print(f"結果: {res}\n")
            
        except ValueError as e:
            print(f"エラー: {e}\n")
        except Exception as e:
            print(f"エラーが発生しました: {e}\n")

def run_api():
    import uvicorn
    # Port 8080 avoids clashing with the Control Server's default 8000.
    uvicorn.run(build_app(), host="0.0.0.0", port=8080)

DEMO_EXPRESSIONS = ["10 + 5", "20 - 8", "6 * 7", "100 / 4", "5 / 0"]

def run_demo():
    print("--- デモ実行 (Automated Mode) ---")
    for expr in DEMO_EXPRESSIONS:
        print(f"実行中: {expr}")
        try:
            print(f" -> 結果: {evaluate_expression(expr)}\n")
        except ValueError as e:
            print(f" -> エラー (期待通り): {e}\n")
    print("対話モードを実行するには、以下のように実行してください:")
    print("  python main.py --interactive")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == "--interactive":
        run_interactive()
    elif mode == "--api":
        run_api()
    else:
        run_demo()
