"""
PayPal PDF Statement Parser
Extracts transaction data from PayPal PDF statements and outputs to CSV
"""
import pdfplumber
import re
import csv
from typing import List, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Transaction:
    """Represents a single PayPal transaction"""
    date: str
    description: str = ''
    currency: str = 'USD'
    amount: str = '0.00'
    fees: str = '0.00'
    total: str = '0.00'
    
    @property
    def clean_payee(self) -> str:
        """Extract clean payee name from description"""
        return PayeeExtractor.extract(self.description)
    
    def to_row(self) -> List[str]:
        """Convert transaction to CSV row"""
        return [
            self.date,
            self.description,
            self.currency,
            self.amount,
            self.fees,
            self.total,
            self.clean_payee
        ]


class PayeeExtractor:
    """Handles extraction of clean payee names from transaction descriptions"""
    
    # Noise words/phrases to filter out
    NOISE_KEYWORDS = {
        'paypal', 'balance', 'usd', 'id:', 'ref id:', 'individual id:',
        'general', 'mastercard', 'debit', 'preapproved payment',
        'bill user payment'
    }
    
    # Prefixes that indicate the payee follows
    PAYEE_PREFIXES = [
        'Transaction:',
        'Direct Deposit:',
        'PreApproved Payment Bill User Payment:',
        'Payment to:',
        'Payment from:'
    ]
    
    @staticmethod
    def extract(description: str) -> str:
        """
        Intelligently extract clean payee name from description.
        Uses pattern recognition rather than hardcoded merchant names.
        """
        if not description or not description.strip():
            return 'Unknown'
        
        # First, try to extract after known prefixes
        payee = PayeeExtractor._extract_after_prefix(description)
        if payee:
            return payee
        
        # If no prefix found, extract the most significant text chunk
        return PayeeExtractor._extract_intelligent(description)
    
    @staticmethod
    def _extract_after_prefix(text: str) -> Optional[str]:
        """Extract text after PayPal transaction prefixes"""
        for prefix in PayeeExtractor.PAYEE_PREFIXES:
            if prefix.lower() in text.lower():
                # Find the prefix case-insensitively
                idx = text.lower().find(prefix.lower())
                after_prefix = text[idx + len(prefix):].strip()
                
                # Clean up the extracted text
                cleaned = PayeeExtractor._clean_text(after_prefix)
                if cleaned:
                    return cleaned
        
        return None
    
    @staticmethod
    def _extract_intelligent(description: str) -> str:
        """
        Intelligently extract payee using text analysis.
        Prioritizes capitalized merchant names and meaningful text.
        """
        # Split into chunks by various delimiters
        chunks = re.split(r'\s{2,}|[|•]', description)
        
        scored_chunks = []
        for chunk in chunks:
            chunk = PayeeExtractor._clean_text(chunk)
            if not chunk:
                continue
            
            score = PayeeExtractor._score_chunk(chunk)
            if score > 0:
                scored_chunks.append((score, chunk))
        
        # Return highest scoring chunk, or first substantial one
        if scored_chunks:
            scored_chunks.sort(reverse=True, key=lambda x: x[0])
            return scored_chunks[0][1]
        
        # Ultimate fallback
        return PayeeExtractor._clean_text(description)[:80] or 'Unknown'
    
    @staticmethod
    def _clean_text(text: str) -> str:
        """Remove amounts, IDs, and noise from text"""
        # Remove amounts (numbers with decimals and commas)
        cleaned = re.sub(r'[-+]?\$?\d{1,3}(?:,\d{3})*\.\d{2}', '', text)
        
        # Remove ID patterns
        cleaned = re.sub(r'\b(ID|Ref ID|Individual ID):\s*\S+', '', cleaned, flags=re.I)
        
        # Remove leading/trailing punctuation and whitespace
        cleaned = cleaned.strip(' |,.-•')
        
        # Check if chunk is mostly noise
        words = cleaned.lower().split()
        if all(word in PayeeExtractor.NOISE_KEYWORDS for word in words if word):
            return ''
        
        # Remove noise words but keep the rest
        filtered_words = [w for w in cleaned.split() 
                         if w.lower() not in PayeeExtractor.NOISE_KEYWORDS]
        
        result = ' '.join(filtered_words).strip()
        
        # Normalize whitespace
        result = re.sub(r'\s+', ' ', result)
        
        return result
    
    @staticmethod
    def _score_chunk(chunk: str) -> int:
        """
        Score a chunk based on likelihood of being a merchant/payee name.
        Higher scores are better.
        """
        score = 0
        
        # Length bonus (prefer substantial text)
        if len(chunk) > 10:
            score += 5
        elif len(chunk) > 5:
            score += 2
        elif len(chunk) < 3:
            return 0  # Too short
        
        # Capitalization patterns (merchant names are often capitalized)
        if chunk[0].isupper():
            score += 3
        
        # Has multiple capital letters (like "ISLAND VIBES LOUNGE")
        if sum(1 for c in chunk if c.isupper()) >= 3:
            score += 4
        
        # Contains location indicators (city, state)
        location_patterns = [
            r'\b[A-Z][a-z]+,\s*[A-Z]{2}\b',  # City, ST
            r'\b[A-Z]{2}\s*\d{5}\b',  # State + ZIP
        ]
        if any(re.search(p, chunk) for p in location_patterns):
            score += 6
        
        # Contains merchant-like identifiers
        merchant_patterns = [
            r'\b[A-Z]+\s+[A-Z]+',  # Multiple capitalized words
            r'\bI-n\d+\b',  # Invoice numbers like "I-n123"
            r'\b#\d+\b',  # Reference numbers
        ]
        if any(re.search(p, chunk) for p in merchant_patterns):
            score += 3
        
        # Penalty for remaining noise words
        noise_count = sum(1 for word in chunk.lower().split() 
                         if word in PayeeExtractor.NOISE_KEYWORDS)
        score -= noise_count * 2
        
        return score


class PayPalPDFParser:
    """Parses PayPal PDF statements and extracts transaction data"""
    
    DATE_PATTERN = re.compile(r'^(\d{1,2}/\d{1,2}/\d{4})')
    AMOUNT_PATTERN = re.compile(r'[-+]?\d{1,3}(?:,\d{3})*\.\d{2}')
    
    JUNK_PATTERNS = [
        r'PayPal Balance.*',
        r'ID:.*',
        r'Individual ID:.*',
        r'Ref ID:.*',
        r'^General PayPal Debit Mastercard\s*$',
        r'^PreApproved Payment Bill User Payment:\s*$',
        r'^General\s*$',
        r'^Transaction:?\s*$',
        r'^Direct Deposit:?\s*$',
        r'^[-•]\s*$',
        r'^\s*$'
    ]
    
    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    def parse(self) -> List[Transaction]:
        """Parse PDF and return list of transactions"""
        transactions = []
        current_transaction = None
        desc_parts = []
        
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    
                    for line in lines:
                        # Skip junk lines
                        if self._is_junk_line(line):
                            continue
                        
                        # Check for date (new transaction)
                        date_match = self.DATE_PATTERN.match(line)
                        if date_match:
                            # Save previous transaction
                            if current_transaction:
                                current_transaction.description = self._clean_description(desc_parts)
                                transactions.append(current_transaction)
                            
                            # Start new transaction
                            current_transaction = Transaction(date=date_match.group(1))
                            rest = line[date_match.end():].strip()
                            desc_parts = [rest] if rest else []
                            
                            # Extract amount from first line if present
                            amounts = self.AMOUNT_PATTERN.findall(rest)
                            if amounts:
                                current_transaction.amount = amounts[-1].replace(',', '')
                                current_transaction.total = current_transaction.amount
                        
                        # Add to current transaction description
                        elif current_transaction:
                            amounts = self.AMOUNT_PATTERN.findall(line)
                            if amounts and not current_transaction.amount:
                                current_transaction.amount = amounts[-1].replace(',', '')
                                current_transaction.total = current_transaction.amount
                            
                            # Clean and add non-amount text
                            clean_text = self.AMOUNT_PATTERN.sub('', line).strip()
                            clean_text = re.sub(r'^[-•]\s*', '', clean_text)
                            if clean_text and len(clean_text) > 2:
                                desc_parts.append(clean_text)
            
            # Save final transaction
            if current_transaction:
                current_transaction.description = self._clean_description(desc_parts)
                transactions.append(current_transaction)
        
        except Exception as e:
            raise RuntimeError(f"Error parsing PDF: {e}") from e
        
        return transactions
    
    def _is_junk_line(self, line: str) -> bool:
        """Check if line matches junk patterns"""
        return any(re.search(pattern, line, re.I) for pattern in self.JUNK_PATTERNS)
    
    def _clean_description(self, parts: List[str]) -> str:
        """Combine and clean description parts"""
        full_desc = ' '.join(parts).strip()
        full_desc = re.sub(r'\s+', ' ', full_desc)
        return full_desc


class CSVWriter:
    """Handles writing transactions to CSV"""
    
    HEADERS = [
        'Date',
        'Full Description',
        'Currency',
        'Amount',
        'Fees',
        'Total',
        'Clean Transaction/Payee'
    ]
    
    @staticmethod
    def write(transactions: List[Transaction], output_path: str):
        """Write transactions to CSV file"""
        output_file = Path(output_path)
        
        try:
            with output_file.open('w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(CSVWriter.HEADERS)
                writer.writerows(t.to_row() for t in transactions)
            
            print(f"✓ Extracted {len(transactions)} transactions → {output_path}")
            print(f"✓ Clean payee column added successfully")
        
        except Exception as e:
            raise RuntimeError(f"Error writing CSV: {e}") from e


def parse_paypal_pdf(pdf_path: str):
    """
    Main function to parse PayPal PDF and export to CSV
    
    Args:
        pdf_path: Path to PayPal PDF statement
        output_csv: Output CSV file path (default: paypal_clean_payee.csv)
    """
    try:
        parser = PayPalPDFParser(pdf_path)
        transactions = parser.parse()
        CSVWriter.write(transactions, str(Path(pdf_path).stem) + ".csv")
    
    except FileNotFoundError as e:
        print(f"✗ Error: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        raise


# Example usage
if __name__ == '__main__':
    parse_paypal_pdf("2025-12-01.pdf")