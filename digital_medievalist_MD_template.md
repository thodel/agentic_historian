# Title of the Article  
*Subtitle (optional)*  

**Authors:**  
- First Name Last Name¹, Institution, Email  
- Second Name Last Name², Institution, Email  

¹ Corresponding author  

**Keywords:** keyword 1; keyword 2; keyword 3; keyword 4  

---

## Abstract  

*Write a concise abstract (≈150 words).*  

---

## Highlights (optional)  

- Highlight 1  
- Highlight 2  
- Highlight 3  

---

## 1 Introduction  

*Introduce the research problem, state objectives and give a brief overview of the article structure.*  

---

## 2 Materials and Methods  

*Describe data, sources, analytical methods, and any experimental procedures.*  

---

## 3 Results  

### 3.1 First Sub-section  

#### Formula example  

Inline formula:  

`$E = mc^2$`  

Displayed formula:  

```math
\[
\frac{\partial v}{\partial t}
= \frac{K}{\mathrm{CD}}\left(
   \frac{\partial^{2}v}{\partial x^{2}}
 + \frac{\partial^{2}v}{\partial y^{2}}
 + \frac{\partial^{2}v}{\partial z^{2}}
 \right)
\]
```

#### Figure example  

![Diagram of the workflow](path/to/figure1.png)

*Figure 1 — Caption describing the workflow. The image should be placed in the same directory as the markdown file (or in a sub-folder referenced by the path).*

#### Table example  

| **Parameter** | **Value** | **Unit** |
|---------------|----------:|----------|
| Sample size   |       120 | —        |
| Mean age      |       45.3| years    |
| Std. dev.     |        5.2| years    |

*Table 1 — Summary of the main quantitative variables.*

#### Footnote example  

Lorem ipsum dolor sit amet, consectetur adipiscing elit.^[This is a footnote providing additional context or a citation.]  

You can add more footnotes throughout the text.

---

## 4 Discussion  

*Interpret the results, compare with previous studies, discuss limitations, and suggest further research.*  

---

## 5 Conclusion  

*Summarise the main findings and their implications.*  

---

## 6 Appendices  

### 6.1 Appendix A – Supplementary Data  

*Include any supplementary tables, figures, code snippets, or extended methodological details.*  

```text
# Example of a code block (Python)
def example_function(x):
    """Return the square of x."""
    return x * x
```  

### 6.2 Appendix B – Additional Figures  

![Supplementary figure](path/to/figure2.png)

*Figure 2 — Caption for the supplementary figure.*

---

## References  

1. Author A., Author B. (Year). *Title of the article*. **Journal Name**, *Volume*(Issue), pages. DOI/URL.  
2. Author C. (Year). *Book Title*. Publisher. ISBN.  
3. …  

*Use the citation style required by the target journal (e.g., APA, Chicago, Vancouver). The numbered list above is converted into a `<listBibl>` bibliography in the generated TEI XML.*  

---

### Notes on the MD → TEI conversion  

The `md_to_tei.py` converter maps each Markdown construct to its TEI OpenEdition equivalent as follows:

| Markdown element | TEI OpenEdition output |
|---|---|
| `# Title` (file-level) | `<titleStmt>/<title type="main">` |
| `*Subtitle*` | `<title type="sub">` |
| `- Author…` (front matter) | `<titleStmt>/<author>` with `<persName>`, `<affiliation>`, `<email>` |
| `**Keywords:**` | `<profileDesc>/<keywords>` |
| `## Abstract` | `<text>/<front>/<div type="abstract">` |
| `## 1 Section` | `<div type="section"><head subtype="level1">` |
| `### 3.1 Subsection` | nested `<div><head subtype="level2">` |
| `#### Formula` (`math` fence) | `<formula notation="latex"><![CDATA[…]]></formula>` |
| `![alt](url)` + caption | `<p rend="figure-title">`, `<figure><graphic url="…"/>`, `<p rend="figure-legend">` |
| Markdown table | `<table><row><cell>…` with `rows`/`cols` for spans |
| `^[footnote]` | `<note place="foot" n="…"><p>…</p></note>` |
| `> quotation` | `<q rend="quotation">` |
| Back-matter references | `<back>/<div type="bibliography"><listBibl><bibl>…` |
| Appendices | `<back>/<div type="appendix">` with nested `<div type="div1">`, `<div type="div2">`… |

Save this file as **`digital_medievalist_MD_template.md`** and run the converter:

```bash
python3 md_to_tei.py digital_medievalist_MD_template.md -o output.tei.xml
```