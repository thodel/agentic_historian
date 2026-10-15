"""ruemlang — a test on one Transkribus collection (Rümlang, Berner Disputation 1528).

Every page that has no segmentation in Transkribus yet is read by three gateway
models (a line model, a page model, TrOCR), the readings are collated line by
line against the 1528 print and arbitrated by the project's text model, and the
result goes back to Transkribus as a new PAGE XML transcript of the same page.

Modules: ``transkribus_pages`` (which pages, and the way back),
``pagexml`` (the XML), ``textalign`` (the print and the line alignment),
``adjudicate`` (the choice), ``pipeline`` (the CLI that strings them together).
"""
