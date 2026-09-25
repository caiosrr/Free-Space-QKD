# Free-Space-QKD: instruções para o Claude

Mestrado do Caio na USP: enlace óptico no espaço livre entre a UFF e o CBPF
(7 km), com um mount ZWO AM5 corrigindo o apontamento a partir da imagem de um
beacon. Três máquinas: o notebook do Caio, onde o código é editado; o PC da UFF,
das sessões longas, com câmera IDS; e o PC do laboratório na USP, da bancada
com DMD e câmera ZWO ASI. O fluxo é editar no notebook, `git push`, e `git pull`
nas outras.

## Documentação de estudo: obrigatória

O código cresce mais rápido do que o Caio consegue revisar. Os documentos de
estudo são o caminho dessa revisão, e mantê-los em dia faz parte de qualquer
mudança, não é tarefa separada.

Ficam em `Anotaçoes/`, numerados na ordem de leitura:

- `01_caminho_de_uma_sessao.md` é a camada 1, o mapa: diz onde cada coisa fica.
- `02_`, `03_`... são a camada 2: leem o código de verdade, um subsistema por
  documento, em texto e blocos de código, na ordem em que o programa executa.

Regras:

1. **Mudou comportamento em código coberto por um documento, atualize o
   documento no mesmo commit.** Mudança só de estilo ou de comentário não exige.
2. **Programa principal ou subsistema novo ganha documento, ou seção nova**,
   antes de ser dado como pronto.
3. **Trechos de código são copiados do arquivo, com caminho e linha**, nunca
   parafraseados. Ao atualizar um documento, reconfira as linhas citadas.
4. **Cada documento traz no topo `Conferido contra o commit <hash>`**, atualizado
   a cada revisão do documento.
5. **Cada seção tem uma marca de revisão que só o Caio altera**:
   `Revisado por Caio: <data>` ou `Revisado por Caio: ainda não`. Se o Claude
   mudar o código de uma seção já revisada, acrescenta logo abaixo da marca
   `Alterado depois da revisão em <data>, commit <hash>: <o que mudou>`. Nunca
   apagar nem editar a marca do Caio.
6. **O que nunca rodou em hardware real é marcado como tal**, explicitamente.
7. **O porquê vem com o número medido**, citando a seção do `roteiro.md` de onde
   ele saiu, em vez de recontar a história.
8. **Depois de mudar um documento, gere o PDF de novo** com
   `python diversos/ferramentas/gerar_pdf_estudo.py`, a partir de `Codigos`. O
   Caio lê e anota os documentos no tablet, pelo Zotero, e o app só abre PDF.
   Os PDFs ficam em `Arquivos/estudo/`, fora do git.

## Segurança do mount: regras fixas

- **Nunca enviar `:hC#` nem `:hP#`** (home e park). O conjunto fica apontado
  quase na horizontal, com cabos presos e câmera montada, e esse movimento pode
  causar colisão. Veto explícito do Caio.
- **Nunca gravar firmware no mount.** Os resultados de segurança valem para os
  firmwares medidos; ver `Anotaçoes/protocolo_am5.md`.
- **Pastas de saída só por `modulos/configuracoes/saidas.py`.** O vigia e a
  trava `mount_em_uso` dependem de enxergar a telemetria exatamente ali.
- **Programa que move o mount checa `motivo_de_uso()` e pede confirmação
  digitada** antes do primeiro movimento.
- **Teste que move o mount só com o Caio ao lado**, avisado antes de rodar.

## Estilo

- Português.
- **Sem travessões** em nenhum texto: nem no chat, nem em documentos, nem em
  mensagens. Pedido explícito do Caio.
- Mensagens que o Caio vai mandar a outras pessoas: curtas e com poucos números.
