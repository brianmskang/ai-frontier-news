from setuptools import setup

setup(
    name="gem-ai-news",
    version="0.1.0",
    py_modules=["scraper"],
    install_requires=[
        "playwright",
        "google-generativeai",
        "python-dotenv",
        "pandas",
        "beautifulsoup4",
        "lxml",
        "python-dateutil",
        "requests"
    ],
    entry_points={
        "console_scripts": [
            "gem-ai-news=scraper:run_cli",
        ],
    },
)
