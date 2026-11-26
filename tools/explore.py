import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anonymize

def main():
    if len(sys.argv) != 2:
        print("Использование: python tools\\explore.py <модель.xml>")
        sys.exit(1)
    classes = defaultdict(int)
    fields = defaultdict(set)
    for elem in anonymize.read_objects(sys.argv[1]):
        cls = anonymize.local_name(elem.tag)
        classes[cls] += 1
        for child in elem:
            fields[cls].add(anonymize.local_name(child.tag))
    for cls, cnt in sorted(classes.items(), key=lambda x: -x[1]):
        print(f"{cls}: {cnt} шт. -> поля: {', '.join(sorted(fields[cls]))}")

if __name__ == "__main__":
    main()
