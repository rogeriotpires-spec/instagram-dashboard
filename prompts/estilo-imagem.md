# Estilo das imagens (vale para pautas diárias, sob demanda e alertas)

Preferência do Rogerio: imagens REALISTAS que retratem a notícia. Padrão para toda option de imagem (type "i") e para a capa de carrossel:

1. **Realista + frase curta** (style "realista"): cena fotográfica realista ocupando a arte inteira, estilo fotojornalismo (luz natural ou de flash, granulação leve, profundidade de campo), mostrando o fato: o lugar, os objetos, a ação (ex.: fuzis apreendidos sobre mesa da PF, viaturas na madrugada, plenário vazio, fachada do STF ao entardecer, fila no INSS). Por cima, NO MÁXIMO uma frase curta e forte (até 7 palavras) em letras grandes e pesadas e, se ajudar, um único número de destaque. Faixa ou sombra escura atrás do texto para leitura no celular.
2. **Selo obrigatório**: no canto inferior, em letras pequenas e discretas, "Imagem ilustrativa". Escreva isso no prompt e inclua em art_text.
3. **Rostos**: NUNCA gere rosto realista de pessoa real (político, policial, ministro, suspeito). Use pessoas de costas, silhuetas, mãos, uniformes sem rosto, multidão desfocada ou só objetos e lugares. Quando o rosto for essencial, indique em faces "usar foto oficial ou de agência enviada pelo Rogerio" e escreva o prompt para editar a foto enviada (fundo, luz, texto), sem alterar o rosto.
4. **Nada que pareça registro real de um fato que não aconteceu**: a cena é ilustrativa e genérica (não reproduza um momento específico, como a prisão de alguém, como se fosse foto do dia). Sem logos, sem marcas de veículos de imprensa, sem brasões oficiais inventados.
5. **Cartaz só de texto** (style "cartaz") apenas quando não houver cena possível (ex.: resultado de pesquisa, placar de votação, comparação de números). Mesmo assim, prefira um fundo fotográfico desfocado ao fundo chapado.
6. Outros valores possíveis de style: "foto-oficial" (edição de foto enviada pelo Rogerio) e "print" (print de manchete, tuíte ou documento).
7. O prompt sempre começa com: "Crie uma imagem fotográfica realista, vertical 4:5 (1080x1350), estilo fotojornalismo..." (ou "Edite a foto enviada..." no caso foto-oficial), descreve a cena com detalhes concretos e termina com o texto exato entre aspas e a posição de cada elemento.

Registre em cada option de imagem ou carrossel o campo "style" com um destes valores: realista, cartaz, foto-oficial, print.
