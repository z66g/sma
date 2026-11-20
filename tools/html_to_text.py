"""HTML → 줄 단위 텍스트 (SEC 공시·기사 원문을 grep 하기 좋게). 사용: curl -s URL | python3 -I tools/html_to_text.py > out.txt"""
import sys,re,html
t=sys.stdin.read()
t=re.sub(r'(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>',' ',t)
t=re.sub(r'(?i)</(p|h[1-6]|li|tr|div)>','\n',t)
t=re.sub(r'<[^>]+>',' ',t)
t=html.unescape(t)
t=re.sub(r'[ \t]+',' ',t); t=re.sub(r'\n\s*\n+','\n',t)
print(t.strip())
