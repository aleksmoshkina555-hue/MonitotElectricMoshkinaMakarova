import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anonymize

def main():
    if len(sys.argv) != 2:
        print("Использование: python tools\\show_words.py <модель.xml>")
        sys.exit(1)
    _, words, streets, protected, _ = anonymize.collect(sys.argv[1])
    print("=== Имена собственные ===")
    for w in sorted(words):
        print(" ", w)
    print("=== Улицы ===")
    for s in sorted(streets):
        print(" ", s)
    print("=== Защищённые слова (районы и стоп-слова) ===")
    for p in sorted(protected):
        print(" ", p)

if __name__ == "__main__":
    main()
