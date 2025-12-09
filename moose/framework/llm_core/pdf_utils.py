"""PDF text extraction utilities using LangChain document loaders."""

from pathlib import Path
from typing import Optional
try:
    from moose.framework.logging import get_core_logger
except ImportError:
    # Fallback for development mode
    from framework.logging import get_core_logger

try:
    from langchain_community.document_loaders import PyPDFLoader
    PDF_LOADER_AVAILABLE = True
except ImportError:
    PDF_LOADER_AVAILABLE = False
    PyPDFLoader = None


def extract_pdf_text(file_path: Path, max_pages: Optional[int] = None) -> str:
    """
    Extract text from PDF file using PyPDFLoader.
    
    Args:
        file_path: Path to the PDF file
        max_pages: Optional maximum number of pages to extract (None for all pages)
    
    Returns:
        Extracted text from the PDF, with pages separated by double newlines
    
    Raises:
        ImportError: If langchain_community is not installed
        FileNotFoundError: If the PDF file doesn't exist
        Exception: If PDF extraction fails
    """
    if not PDF_LOADER_AVAILABLE:
        raise ImportError(
            "langchain_community is required for PDF extraction. "
            "Install it with: pip install langchain-community"
        )
    
    logger = get_core_logger()
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")
    
    logger.debug(f"Extracting text from PDF: {file_path}")
    
    try:
        loader = PyPDFLoader(str(file_path))
        documents = loader.load()
        
        # Limit pages if max_pages is specified
        if max_pages is not None and len(documents) > max_pages:
            documents = documents[:max_pages]
            logger.debug(f"Limited extraction to first {max_pages} pages")
        
        # Combine all page contents
        text = "\n\n".join([doc.page_content for doc in documents])
        
        logger.debug(f"Extracted {len(documents)} pages from PDF ({len(text)} characters)")
        return text
        
    except Exception as e:
        logger.error(f"Failed to extract text from PDF {file_path}: {e}")
        raise

