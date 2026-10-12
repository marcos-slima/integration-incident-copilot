"""Harness de refactoring: mede a qualidade de EDICAO DE CODIGO por modelo.

Por que este pacote existe
--------------------------

A suite de diagnostico (`promptfooconfig.yaml`) mede o pipeline de RAG +
rule engine, nao a capacidade de editar codigo. Ela deliberadamente NAO
consegue discriminar os modelos locais (medido em 2026-10-11: os quatro
empatam nos casos dificeis). Ou seja: a dimensao que decide o ambiente de
trabalho do OpenCode - "o modelo edita codigo corretamente, com precisao,
sem quebrar o que existia?" - nunca foi medida por nada neste repositorio.

Este harness fecha essa lacuna. Ele NAO avalia o modelo em abstrato
("o modelo sabe programar?"); ele avalia uma TAREFA DE EDICAO com contrato
explicito e verificacao DETERMINISTICA:

    fixture (codigo) -> instrucao -> modelo edita via OpenCode
                    -> pytest oculto decide passou/falhou

O veredito nunca vem do modelo. Vem de `pytest` rodando um teste que o
modelo nao viu. Mesma disciplina da invariante 4 (nao usar auto-avaliacao
do LLM como evidencia).

Tres eixos, nao um
------------------

- ACERTO   -> o pytest oculto passa? (por tarefa, x repeticoes)
- PRECISAO -> o modelo editou so o que devia? (arquivos congelados) e
              quanto mexeu? (`git diff --stat`)
- VELOCIDADE -> wall-clock, tokens/s e numero de passos, lidos do proprio
              stream NDJSON do `opencode run --format json`

O que este harness NAO autoriza
-------------------------------

Um placar alto aqui NAO promove um modelo a default do projeto sozinho:
o harness mede EDICAO, nao tool-calling sob ambiguedade nem o loop
agentico completo. Ele existe para transformar "achei o modelo bom" em
"este modelo passou N/N com diff de X linhas", que e o que a DA-8 exigiu
("confiabilidade sob o caso mais exigente pesa mais que media").
"""
