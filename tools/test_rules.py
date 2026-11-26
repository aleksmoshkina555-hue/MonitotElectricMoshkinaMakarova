import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anonymize

def main():
    anonymize.SECRET = "test-secret"
    used = set()
    cnt = {"persons": 0, "base64": 0, "numbers": set()}
    wmap = {"Ngolona": "Alychovaya", "Kangaba": "Arbuznaya"}
    smap = {"Djikoroni": "Grigorevskaya"}

    tests = [
        ("Sub 35 kV Ngolona", "Sub 35 kV Alychovaya"),
        ("OHL 35 kV Ngolona - Kangaba", "OHL 35 kV Alychovaya - Arbuznaya"),
        ("abs(ПС_Ngolona_P_Ф_01)", "abs(ПС_Alychovaya_P_Ф_01)"),
        ("Djikoroni Street", "Grigorevskaya Street"),
        ("Koulikoro District", "Koulikoro District"),
    ]
    passed = 0
    for idx in range(20):
        src, expected = tests[idx % len(tests)]
        got = anonymize.process_text(src, wmap, smap, used, cnt)
        assert got == expected, f"Ошибка теста {idx}: {got} != {expected}"
        passed += 1
    print(f"Тесты правил пройдены: {passed} из 20 OK.")

if __name__ == "__main__":
    main()
