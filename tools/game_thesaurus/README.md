# 게임 사운드 동의어 사전 만들기 (시험용, 2026-10-13)

결과물: `py/app/data/game_sfx_thesaurus.tsv` — 베이스헤드 사전과 같은 꼴(`ID<TAB>말<TAB>말…`, 첫 줄은 머리).
실행: 개인판 폴더의 `run_gamedict.bat` (설치본은 그대로 UCS 사전).

## 크기 (베이스헤드 원본 118묶음·384개 말 대비)

| | 베이스헤드 원본 | 새 사전 | 배수 |
|---|---|---|---|
| 묶음 | 118 | 1,822 | 15.4배 |
| 말 | 384 | 4,742 | 12.3배 |
| 서로 다른 말 | 359 | 4,076 | 11.4배 |

꼴만 다른 묶음(lion·lions)은 검색이 이미 찾으므로(단어 꼴 처리) 셈에서도, 사전에서도 뺐다.

## 출처 (merge_report.json 에 묶음마다 출처 기록)

| 출처 | 내용 | 스크립트 |
|---|---|---|
| 베이스헤드 원본 | `C:\Program Files\basehead\Thesaurus.tsv` 그대로 (분포가 완전히 다른 말만 뺌: imp↔impact 등) | merge.py |
| 직접 정리 | 게임 사운드 전용 묶음 — UI·게임 진행·능력/마법·무기·근접전·캐릭터·크리처·환경·탈것·SF·사운드 디자인·악기·생활 소품·자연·스포츠·산업·감정 | curated_game.json, curated_game2.json |
| UCS 공식 약어 | UCS v8.2.1 대분류·하위분류 약어 ↔ 원래 이름 (EXPL↔explosion, Brk↔break) | src_ucs.py |
| 붙여 쓰기 | 실제 영어 단어인 붙여 쓴 말 ↔ 띄어 쓴 꼴 (gunshot↔'gun shot') — 라이브러리에 띄어 쓴 꼴이 실제로 있는 것 | src_ortho.py |
| 미국/영국 철자 | 직접 정리한 철자 쌍 (armor↔armour) — 뜻이 여럿인 낱말 차이(fall/autumn 등)는 뺌 | uk_us_pairs.json |
| 늘여 쓴 의성어 | hmmm↔hmm, shhh↔shh (모음·h·m·s·y 늘임만) | src_ortho.py |
| 라이브러리 줄임말 | 라이브러리에 실제로 쓰이는 줄임말 (mvmt, impt, swrd, enrg) | src_abbr.py |
| 영어 동의어 DB | Datamuse(WordNet) 동의어 중 라이브러리에서 같은 종류 소리에 쓰이는 것 | fetch_datamuse.py, src_wordnet.py |

## 혼입을 막은 방법

1. **라이브러리 분포 검사** (build_profiles.py → simlib.py): 분류(UCS 대분류·하위분류)가 붙은 파일 54만 개로
   단어마다 "어떤 소리에 쓰이나" 분포를 만들고, 두 말이 **평균보다 특히 많이 쓰이는 분류가 겹치는지**(PPMI)를 본다.
   어디에나 쓰이는 일반 단어끼리(by↔away)는 자동으로 낮게 나온다.
2. **아주 흔한 영어 단어 제외** (wordcheck.py, Datamuse 빈도): 영어 동의어 DB 출처에서 100만 단어당 40회 이상인 말.
3. **회사·라이브러리 이름 겹침** (merge.py): 걸리는 파일 절반 이상이 그 말이 이름에 든 라이브러리 안이면 뺌
   (boom→BOOM Library, morph→SoundMorph).
4. **직접 검토** (review_rules.json): 자동 출처 전부를 한 줄씩 보고 뺀 묶음·말.
   예: scream|cry|shout, slosh|splash, torch|blowtorch, gunshot|'guns hot', frantic|frnt, mag(=magic, 탄창과 겹침).

## 다시 만들기

```
python build_profiles.py     # 라이브러리 분포표 (39초) → profiles.pkl
python src_ucs.py
python src_ortho.py
python src_abbr.py
python fetch_datamuse.py     # 영어 동의어 조회 → datamuse_syn.json (약 15분, 캐시는 레포에 없음)
python src_wordnet.py
python merge.py              # → game_sfx_thesaurus.tsv, merge_report.json
copy game_sfx_thesaurus.tsv ..\..\py\app\data\
```

⚠ 결과는 이 PC 의 인덱스(라이브러리)에 맞춰 걸러진다 — 라이브러리가 크게 바뀌면 다시 돌릴 것.
⚠ 사전에 베이스헤드 원본 내용이 들어 있다 (사용자 결정 2026-10-13: 비상업 사용).
