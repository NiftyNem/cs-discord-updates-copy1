def clean_html(raw_html):
    # 1. Pre-procesado: Steam a veces escapa los corchetes \[ ]
    raw_html = raw_html.replace(r'\[', '[').replace(r'\]', ']')
    
    soup = BeautifulSoup(raw_html, "html.parser")

    # Headers Steam (bb_h3)
    for h in soup.select(".bb_h3"):
        if h.parent:
            h.replace_with(f"\n### {h.get_text(strip=True)}\n")

    # Links → Markdown (Discord format)
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text and href and a.parent:
            # Si el texto y el link son iguales, solo ponemos el link
            replacement = f"[{text}]({href})" if text != href else href
            a.replace_with(replacement)

    # Imágenes (solo la primera para el cuerpo del mensaje)
    images = soup.find_all("img")
    for i, img in enumerate(images):
        src = img.get("src", "")
        if i == 0 and src and img.parent:
            img.replace_with(f"\n{src}\n")
        else:
            img.decompose()

    # Listas (UL / LI) - Procesamos de abajo hacia arriba
    for ul in reversed(soup.find_all("ul")):
        if not ul.parent:
            continue
            
        # Si es una lista anidada (UL dentro de LI), dejamos que el padre la gestione
        if ul.parent.name == "li":
            continue

        lines = []
        # Buscamos todos los LI dentro de esta lista
        for li in ul.find_all("li"):
            # Calculamos indentación básica si hay niveles
            parent_lists = len(li.find_parents("ul")) - 1
            indent = "  " * parent_lists
            lines.append(f"{indent}• {li.get_text(strip=True)}")
        
        ul.replace_with("\n" + "\n".join(lines) + "\n")

    # Br y Párrafos
    for br in soup.find_all("br"):
        if br.parent: br.replace_with("\n")
            
    for p in soup.find_all(["p", "div"]):
        if p.parent:
            # Añadimos saltos de línea alrededor de bloques de texto
            p.insert_before("\n")
            p.insert_after("\n")
            p.unwrap() # Quitamos la etiqueta pero dejamos el texto

    # Obtener el texto final
    text = soup.get_text()

    # --- POST-PROCESADO DE TEXTO ---
    
    # 2. Convertir encabezados tipo [ GAMEPLAY ] a Markdown
    text = re.sub(
        r'\[\s*([A-Z0-9\s\-_]+)\s*\]',
        lambda m: f"\n**{m.group(1).strip()}**",
        text
    )

    # 3. Limpiar múltiples saltos de línea y espacios raros
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' , ', ', ', text) # Limpia comas sueltas
    text = re.sub(r' +', ' ', text)   # Limpia espacios dobles

    return text.strip()
