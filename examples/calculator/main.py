"""Sample calculator application utilizing @probe for execution tracing and shadow mode.

Run:
    $env:PROBE_SERVER_URL="http://localhost:8000"
    python main.py
"""

import sys
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

def run_interactive():
    print("--- \u96fb\u535e\u30a2\u30d7\u30ea (Interactive Mode) ---")
    print("\u5404\u6f14\u7b97\u3092\u5b9f\u884c\u3059\u308b\u306b\u306f\u3001\u88cf\u3067 PROBE \u306e\u30c8\u30ec\u30fc\u30b9\u304c\u884c\u308f\u308c\u307e\u3059\u3002")
    print("\u7d42\u4e86\u3059\u308b\u306b\u306f 'exit' \u3092\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044\u3002\n")
    
    while True:
        try:
            user_input = input("\u5f0f\u3092\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044 (\u4f8b: 5 + 3, 10 / 2) -> ").strip()
            if user_input.lower() == "exit":
                break
            
            parts = user_input.split()
            if len(parts) != 3:
                print("\u30a8\u30e9\u30fc: '\u6570 \u6f14\u7b97\u5b50 \u6570' \u306e\u5f62\u5f0f\u3067\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044 (\u4f8b: 5 + 3)")
                continue
            
            a_val, op, b_val = parts
            a = float(a_val)
            b = float(b_val)
            
            res = 0.0
            if op == "+":
                res = run_add(a, b)
            elif op == "-":
                res = run_subtract(a, b)
            elif op == "*":
                res = run_multiply(a, b)
            elif op == "/":
                res = run_divide(a, b)
            else:
                print(f"\u30a8\u30e9\u30fc: \u30b5\u30dd\u30fc\u30c8\u3055\u308c\u3066\u3044\u306a\u3044\u6f14\u7b97\u5b50\u3067\u3059: {op}")
                continue
                
            print(f"\u7d50\u679c: {res}\n")
            
        except ValueError as e:
            print(f"\u30a8\u30e9\u30fc: \u5165\u529b\u5024\u304c\u4e0d\u6b63\u3067\u3059\u3002 {e}\n")
        except Exception as e:
            print(f"\u30a8\u30e9\u30fc\u304c\u767a\u751f\u3057\u307e\u3057\u305f: {e}\n")

def run_demo():
    print("--- \u30c7\u30e2\u5b9f\u884c (Automated Mode) ---")
    demo_cases = [
        (10.0, "+", 5.0),
        (20.0, "-", 8.0),
        (6.0, "*", 7.0),
        (100.0, "/", 4.0),
        (5.0, "/", 0.0)  # ゼロ\u9664\u7b97\u306e\u30c6\u30b9\u30c8
    ]
    
    for a, op, b in demo_cases:
        print(f"\u5b9f\u884c\u4e2d: {a} {op} {b}")
        try:
            res = 0.0
            if op == "+":
                res = run_add(a, b)
            elif op == "-":
                res = run_subtract(a, b)
            elif op == "*":
                res = run_multiply(a, b)
            elif op == "/":
                res = run_divide(a, b)
            print(f" -> \u7d50\u679c: {res}\n")
        except Exception as e:
            print(f" -> \u30a8\u30e9\u30fc (\u671f\u5f85\u901a\u308a): {e}\n")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        run_interactive()
    else:
        run_demo()
        print("\n\u5bfe\u8a71\u30e2\u30fc\u30c9\u3092\u5b9f\u884c\u3059\u308b\u306b\u306f\u3001\u4ee5\u4e0b\u306e\u3088\u3046\u306b\u5b9f\u884c\u3057\u3066\u304f\u3060\u3055\u3044:")
        print("  python main.py --interactive")
