import streamlit as st
import requests
import os
from datetime import datetime, timedelta
import re
import hashlib
import json

# ===============================
# SUPABASE CACHE CLASS - FIXED VERSION
# ===============================
class SupabaseCache:
    def __init__(self, ttl_days=7):
        """
        Supabase-based cache for multi-user, persistent storage
        """
        self.ttl_days = ttl_days
        
        # Get Supabase credentials from environment
        self.supabase_url = os.environ.get("SUPABASE_URL", "")
        self.supabase_key = os.environ.get("SUPABASE_KEY", "")
        
        # Initialize Supabase client
        self.supabase = None
        self._init_supabase()
        
        # In-memory fallback cache
        self.memory_cache = {}
        self.max_memory_entries = 100
        
    def _init_supabase(self):
        """Initialize Supabase client"""
        if self.supabase_url and self.supabase_key:
            try:
                from supabase import create_client
                self.supabase = create_client(self.supabase_url, self.supabase_key)
                # Test connection
                self.supabase.table("seba_cache").select("count", count="exact").limit(1).execute()
            except ImportError:
                # supabase-py not installed
                self.supabase = None
            except Exception as e:
                # Connection failed
                print(f"Supabase connection error: {e}")
                self.supabase = None
        else:
            self.supabase = None
    
    def get(self, cache_key):
        """Get cached answer - try Supabase first, then memory"""
        # First check memory cache (fastest)
        if cache_key in self.memory_cache:
            entry = self.memory_cache[cache_key]
            if self._is_valid(entry):
                entry['access_count'] = entry.get('access_count', 0) + 1
                entry['last_accessed'] = datetime.now().isoformat()
                return entry
        
        # Try Supabase if available - FIXED: Removed TTL filter from query
        if self.supabase:
            try:
                # Get entry without TTL filter - we'll check TTL in Python
                response = self.supabase.table("seba_cache") \
                    .select("*") \
                    .eq("key_hash", cache_key) \
                    .execute()
                
                if response.data and len(response.data) > 0:
                    entry = response.data[0]
                    
                    # Check if entry is expired
                    created_at_str = entry.get('created_at')
                    is_expired = False
                    
                    if created_at_str:
                        try:
                            # Parse the timestamp
                            if 'Z' in created_at_str:
                                created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            else:
                                created_at = datetime.fromisoformat(created_at_str)
                            
                            # Check TTL
                            if (datetime.now() - created_at).days >= self.ttl_days:
                                # Entry expired, delete it
                                is_expired = True
                                try:
                                    self.supabase.table("seba_cache") \
                                        .delete() \
                                        .eq("key_hash", cache_key) \
                                        .execute()
                                except:
                                    pass
                        except Exception:
                            # If we can't parse date, assume not expired
                            pass
                    
                    if not is_expired:
                        # Convert to standard format
                        cached_data = {
                            'answer': entry['answer'],
                            'tokens': entry.get('tokens', 0),
                            'subject': entry.get('subject', ''),
                            'chapter': entry.get('chapter', ''),
                            'question': entry.get('question', ''),
                            'access_count': entry.get('access_count', 0) + 1,
                            'created_at': entry.get('created_at'),
                            'last_accessed': datetime.now().isoformat()
                        }
                        
                        # Update access count in Supabase
                        try:
                            self.supabase.table("seba_cache") \
                                .update({
                                    "last_accessed": datetime.now().isoformat(),
                                    "access_count": entry.get('access_count', 0) + 1
                                }) \
                                .eq("key_hash", cache_key) \
                                .execute()
                        except:
                            pass
                        
                        # Store in memory cache for faster access
                        self.memory_cache[cache_key] = cached_data
                        
                        # Limit memory cache size
                        if len(self.memory_cache) > self.max_memory_entries:
                            oldest_key = min(self.memory_cache.keys(), 
                                            key=lambda k: self.memory_cache[k].get('last_accessed', ''))
                            del self.memory_cache[oldest_key]
                        
                        return cached_data
            except Exception as e:
                # Silently fail - fall back to memory cache
                print(f"Supabase get error: {e}")
                pass
        
        return None
    
    def set(self, cache_key, data):
        """Store answer in both Supabase and memory cache"""
        # Prepare data
        cache_data = {
            'answer': data['answer'],
            'tokens': data.get('tokens', 0),
            'subject': data.get('subject', ''),
            'chapter': data.get('chapter', ''),
            'question': data.get('question', '')[:200],
            'access_count': 1,
            'created_at': datetime.now().isoformat(),
            'last_accessed': datetime.now().isoformat()
        }
        
        # Store in memory cache
        self.memory_cache[cache_key] = cache_data
        
        # Limit memory cache size
        if len(self.memory_cache) > self.max_memory_entries:
            oldest_key = min(self.memory_cache.keys(), 
                            key=lambda k: self.memory_cache[k].get('last_accessed', ''))
            del self.memory_cache[oldest_key]
        
        # Store in Supabase if available
        if self.supabase:
            try:
                self.supabase.table("seba_cache").upsert({
                    "key_hash": cache_key,
                    "question": cache_data['question'],
                    "answer": cache_data['answer'],
                    "subject": cache_data['subject'],
                    "chapter": cache_data['chapter'],
                    "tokens": cache_data['tokens'],
                    "created_at": cache_data['created_at'],
                    "last_accessed": cache_data['last_accessed'],
                    "access_count": cache_data['access_count']
                }).execute()
            except Exception as e:
                # Silently fail - at least we have memory cache
                print(f"Supabase set error: {e}")
                pass
    
    def _is_valid(self, entry):
        """Check if cache entry is not expired"""
        created_at = entry.get('created_at')
        if isinstance(created_at, str):
            try:
                if 'Z' in created_at:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    created_at = datetime.fromisoformat(created_at)
            except:
                return True  # If we can't parse, assume valid
        
        if created_at and (datetime.now() - created_at).days < self.ttl_days:
            return True
        return False
    
    def clear_expired(self):
        """Clear expired entries from memory cache"""
        expired_keys = []
        for key, entry in self.memory_cache.items():
            if not self._is_valid(entry):
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.memory_cache[key]
        
        return len(expired_keys)
    
    def clear_all(self):
        """Clear all cache entries"""
        self.memory_cache = {}
        
        # Also clear Supabase cache if available
        if self.supabase:
            try:
                # Delete entries older than 1 day (safer than deleting all)
                self.supabase.table("seba_cache") \
                    .delete() \
                    .lt("created_at", f"now() - interval '1 day'") \
                    .execute()
            except:
                pass
    
    def get_stats(self):
        """Get cache statistics"""
        # Memory cache stats
        memory_entries = len(self.memory_cache)
        memory_tokens = sum(entry.get('tokens', 0) for entry in self.memory_cache.values())
        
        # Try to get Supabase stats
        supabase_entries = 0
        supabase_tokens = 0
        
        if self.supabase:
            try:
                # Get count from Supabase
                response = self.supabase.table("seba_cache") \
                    .select("count", count="exact") \
                    .execute()
                
                supabase_entries = response.count or 0
                
                # Get total tokens (might be heavy, so approximate)
                if supabase_entries > 0:
                    response = self.supabase.table("seba_cache") \
                        .select("tokens") \
                        .limit(100) \
                        .execute()
                    
                    supabase_tokens = sum(entry.get('tokens', 0) for entry in response.data)
            except:
                pass
        
        total_entries = memory_entries + supabase_entries
        total_tokens = memory_tokens + supabase_tokens
        
        return {
            'total_entries': total_entries,
            'memory_entries': memory_entries,
            'supabase_entries': supabase_entries,
            'total_saved_tokens': total_tokens,
            'ttl_days': self.ttl_days,
            'storage_mode': 'Supabase + Memory' if self.supabase else 'Memory Only',
            'supabase_connected': self.supabase is not None
        }

# ===============================
# API KEY HANDLING
# ===============================
# Get API key from environment variable
api_key = os.environ.get("DEEPSEEK_API_KEY", "")

# Page config - must be first Streamlit command
st.set_page_config(
    page_title="SEBA দশম শ্ৰেণীৰ AI টিউটাৰ",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Simplified CSS with reduced spacing (50% of original)
st.markdown("""
<style>
    /* Assamese-friendly fonts */
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600;700;800&family=Hind+Siliguri:wght@300;400;500;600;700&display=swap');
    
    * {
        font-family: 'Noto Sans Bengali', 'Hind Siliguri', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    /* Reduced spacing header */
    .header-container {
        background: linear-gradient(135deg, #0d47a1 0%, #1565c0 50%, #1976d2 100%);
        padding: 1.25rem;
        border-radius: 15px;
        margin-bottom: 1rem;
        color: white;
        position: relative;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.1);
        box-shadow: 0 5px 15px rgba(13,71,161,0.2);
    }

    /* The top rainbow line */
    .header-container::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: linear-gradient(90deg, #FF5722, #FF9800, #4CAF50);
    }

    /* Header text */
    .header-container h1 {
        color: #ffffff;
        font-size: 1.5rem;
        font-weight: 800;
        text-shadow: 0px 1px 3px rgba(0,0,0,0.5);
        margin: 0;
        line-height: 1.2;
    }

    .header-container p {
        color: #f6f9ff;
        font-weight: 600;
        font-size: 0.95rem;
        opacity: 1 !important;
        text-shadow: 0px 1px 2px rgba(0,0,0,0.4);
        margin-top: .2rem;
    }

    .subject-card {
        background: linear-gradient(145deg, #ffffff 0%, #f0f7ff 100%);
        padding: 0.75rem;
        border-radius: 10px;
        box-shadow: 0 3px 8px rgba(0, 0, 0, 0.08);
        border-left: 4px solid #2196F3;
        margin: 0.5rem 0;
        transition: all 0.3s ease;
        border: 1px solid #e3f2fd;
    }
    
    .subject-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 5px 12px rgba(33, 150, 243, 0.15);
    }
    
    .answer-box {
        background: linear-gradient(145deg, #f8fdff 0%, #ffffff 100%);
        padding: 1rem;
        border-radius: 10px;
        border: 1px solid #e1f5fe;
        margin: 0.75rem 0;
        box-shadow: 0 3px 8px rgba(0, 0, 0, 0.05);
        position: relative;
    }
    
    .stButton > button {
        background: linear-gradient(135deg, #FF5722 0%, #FF9800 100%);
        color: white;
        border: none;
        padding: 0.4rem 1rem;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        transition: all 0.3s;
        box-shadow: 0 2px 6px rgba(255, 87, 34, 0.3);
    }
    
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 3px 9px rgba(255, 87, 34, 0.4);
    }
    
    .sidebar-section {
        background: linear-gradient(145deg, #f8f9fa 0%, #e3f2fd 100%);
        padding: 0.75rem;
        border-radius: 10px;
        margin-bottom: 0.75rem;
        border: 1px solid #bbdefb;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
    }
    
    .assamese-highlight {
    background: linear-gradient(120deg, #FFF176 0%, #FFEB3B 100%);
    background-repeat: no-repeat;
    background-size: 100% 0.3em;
    background-position: 0 90%;
    padding: 0.1rem 0.2rem;
    font-weight: 700;
    color: #FF6F00;
}
    
    .assamese-text {
        font-family: 'Noto Sans Bengali', sans-serif;
        font-weight: 500;
        color: #0d47a1;
        line-height: 1.4;
    }
    
    .assamese-title {
        font-family: 'Noto Sans Bengali', sans-serif;
        font-weight: 700;
        color: #1565c0;
    }
    
    /* Chat bubble styling */
    .user-bubble {
        background: linear-gradient(135deg, #2196F3 0%, #0d47a1 100%) !important;
        color: white;
        padding: 0.5rem 0.75rem;
        border-radius: 12px 12px 0 12px;
        max-width: 80%;
        box-shadow: 0 2px 6px rgba(33, 150, 243, 0.2);
        margin-left: auto;
    }
    
    .ai-bubble {
        background: linear-gradient(135deg, #f5f5f5 0%, #ffffff 100%) !important;
        padding: 0.75rem;
        border-radius: 12px 12px 12px 0;
        border: 1px solid #e0e0e0;
        box-shadow: 0 2px 6px rgba(0,0,0,0.05);
    }
    
    /* Chat container */
    .chat-container {
        margin-bottom: 1rem;
    }
    
    .chat-message {
        margin-bottom: 0.75rem;
        animation: fadeIn 0.3s ease-in;
    }
    
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(5px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    /* LaTeX equation styling */
    .katex {
        font-size: 1em !important;
        padding: 0.1rem 0.25rem;
        background: rgba(33, 150, 243, 0.1);
        border-radius: 3px;
        margin: 0.1rem 0;
    }
    
    /* Control panel styling */
    .control-panel {
        background: linear-gradient(145deg, #f8f9fa 0%, #e3f2fd 100%);
        padding: 1rem;
        border-radius: 15px;
        margin: 1rem 0;
        border: 1px solid #bbdefb;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    }
    
    /* Streaming text animation */
    .streaming-text {
        display: inline-block;
    }
    
    .streaming-text::after {
        content: '▋';
        animation: cursor-blink 1s infinite;
        font-weight: bold;
        color: #2196F3;
    }
    
    @keyframes cursor-blink {
        0%, 100% { opacity: 1; }
        50% { opacity: 0; }
    }
    
    /* Progress indicator */
    .progress-indicator {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        color: #0d47a1;
        font-weight: 600;
        padding: 0.5rem;
    }
    
    .thinking-dots {
        display: flex;
        gap: 0.2rem;
    }
    
    .thinking-dots span {
        width: 0.4rem;
        height: 0.4rem;
        border-radius: 50%;
        background: #2196F3;
        animation: thinking 1.4s infinite ease-in-out;
    }
    
    .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
    .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
    .thinking-dots span:nth-child(3) { animation-delay: 0s; }
    
    @keyframes thinking {
        0%, 80%, 100% { transform: scale(0); }
        40% { transform: scale(1); }
    }
    
    /* Cache indicator styling */
    .cache-badge {
        background: linear-gradient(135deg, #4CAF50 0%, #2E7D32 100%);
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        display: inline-flex;
        align-items: center;
        gap: 0.2rem;
    }
    
    /* Responsive adjustments */
    @media (max-width: 768px) {
        .header-container {
            padding: 0.75rem;
        }
        .subject-card {
            padding: 0.5rem;
        }
        .user-bubble, .ai-bubble {
            max-width: 90%;
        }
        .control-panel {
            padding: 0.75rem;
        }
    }
</style>
""", unsafe_allow_html=True)

# ===============================
# SEBA CURRICULUM DATA
# ===============================
SEBA_CURRICULUM = {
    "📐 গণিত (Mathematics)": {
        "অধ্যায় ১": "বাস্তৱ সংখ্যা (Real Numbers)",
        "অধ্যায় ২": "বহুপদ (Polynomials)",
        "অধ্যায় ৩": "দ্বিঘাত সমীকৰণ (Quadratic Equations)",
        "অধ্যায় ৪": "সামান্তৰিক শ্রেণী (Arithmetic Progressions)",
        "অধ্যায় ৫": "ত্ৰিভুজ (Triangles)",
        "অধ্যায় ৬": "ত্রিকোণমিতি (Trigonometry)",
        "অধ্যায় ৭": "বৃত্ত (Circles)",
        "অধ্যায় ৮": "স্থানাঙ্ক জ্যামিতি (Coordinate Geometry)",
        "অধ্যায় ৯": "ক্ষেত্রফল আৰু আয়তন (Areas and Volumes)",
        "অধ্যায় ১০": "পৰিসংখ্যা (Statistics)",
        "অধ্যায় ১১": "সম্ভাৱিতা (Probability)"
    },
    "🔬 বিজ্ঞান (Science)": {
        "অধ্যায় ১": "ৰাসায়নিক বিক্রিয়া আৰু সমীকৰণ",
        "অধ্যায় ২": "এছিড, ক্ষাৰক আৰু লৱণ",
        "অধ্যায় ৩": "ধাতু আৰু অধাতু",
        "অধ্যায় ৪": "কার্বন আৰু তাৰ যৌগ",
        "অধ্যায় ৫": "পৰ্যাবৃত্ত শ্রেণীবিভাজন",
        "অধ্যায় ৬": "জীৱন প্ৰক্ৰিয়া",
        "অধ্যায় ৭": "নিয়ন্ত্ৰণ আৰু সমন্বয়",
        "অধ্যায় ৮": "জীৱই কেনেদৰে বংশবিস্তাৰ কৰে",
        "অধ্যায় ৯": "আনুভূমিক আৰু ঊর্ধ্বমুখী বংশগতি",
        "অধ্যায় ১০": "পোহৰ-প্ৰতিফলন আৰু প্ৰতিসৰণ",
        "অধ্যায় ১১": "মানুহৰ চকু আৰু বৰ্ণিল পৃথিৱী",
        "অধ্যায় ১২": "বিদ্যুৎ",
        "অধ্যায় ১৩": "বিদ্যুৎ-চুম্বকীয় প্ৰভাৱ",
        "অধ্যায় ১৪": "শক্তিৰ উৎসসমূহ",
        "অধ্যায় ১৫": "আমাৰ পৰিৱেশ",
        "অধ্যায় ১৬": "প্রাকৃতিক সম্পদৰ ব্যৱস্থাপনা"
    },
    "🌍 সমাজ বিজ্ঞান (Social Science)": {
        "অধ্যায় ১": "ইউৰোপত ৰাষ্ট্ৰবাদৰ উত্থান",
        "অধ্যায় ২": "ভাৰতীয় জাতীয়তাবাদৰ উত্থান",
        "অধ্যায় ৩": "ভূগোল-প্রাকৃতিক আৰু মানৱ",
        "অধ্যায় ৪": "অৰ্থনীতি-উন্নয়ন",
        "অধ্যায় ৫": "লোকসাধাৰণৰ সংস্কৃতি আৰু জাতীয়তাবাদ",
        "অধ্যায় ৬": "উদ্যোগ",
        "অধ্যায় ৭": "অৰ্থনৈতিক অৱস্থা",
        "অধ্যায় ৮": "ৰাজনৈতিক দল",
        "অধ্যায় ৯": "ক্ষমতাৰ ভাগ-বতৰা",
        "অধ্যায় ১০": "জনসম্পদ"
    },
    "📖 ইংৰাজী (English)": {
        "পাঠ ১": "A Letter to God",
        "পাঠ ২": "Nelson Mandela: Long Walk to Freedom",
        "পাঠ ৩": "Two Stories about Flying",
        "পাঠ ৪": "From the Diary of Anne Frank",
        "পাঠ ৫": "The Hundred Dresses – I",
        "পাঠ ৬": "The Hundred Dresses – II",
        "পাঠ ৭": "Glimpses of India",
        "পাঠ ৮": "Mijbil the Otter",
        "পাঠ ৯": "Madam Rides the Bus",
        "পাঠ ১০": "The Sermon at Benares",
        "পাঠ ১১": "The Proposal"
    },
    "📜 অসমীয়া (Assamese)": {
        "পাঠ ১": "বৰগীত",
        "পাঠ ২": "জীৱন-সঙ্গীত",
        "পাঠ ৩": "প্রশস্তি",
        "পাঠ ৪": "মোৰ মৰমি জনমভূমি",
        "পাঠ ৫": "অসমীয়া ভাষাৰ উন্নতি",
        "পাঠ ৬": "অসমৰ লোক-সংস্কৃতি",
        "পাঠ ৭": "আমাৰ ঋতু",
        "পাঠ ৮": "বহাগ বিহু",
        "পাঠ ৯": "মহাপুরুষীয়া ধৰ্ম",
        "পাঠ ১০": "সাহিত্যৰ ৰূপ"
    },
    "📘 হিন্দী (Hindi)": {
        "পাঠ ১": "साखी",
        "পাঠ ২": "पद",
        "পাঠ ৩": "दोहे",
        "পাঠ ৪": "मनुष्यता",
        "পাঠ ৫": "पर्वत प्रदेश में पावस",
        "পাঠ ৬": "मधुर-मधुर मेरे दीपक जल",
        "পাঠ ৭": "तोप",
        "পাঠ ৮": "कर चले हम फ़िदा",
        "পাঠ ৯": "आत्मत्राण",
        "পাঠ ১০": "बड़े भाई साहब"
    }
}

# Subject-wise prompt templates
SUBJECT_PROMPTS = {
    "📐 গণিত (Mathematics)": {
        "base_prompt": """তুমি এজন বিশেষজ্ঞ গণিত শিক্ষক। SEBA দশম শ্ৰেণীৰ গণিতৰ পাঠ্যপুথিৰ {chapter_name} অধ্যায়ত থকা সকলো ধাৰণা, সূত্ৰ, আৰু উদাহৰণ তুমি ভালকৈ জানা।

**গণিতৰ বিশেষ নিৰ্দেশনা:**
১. **সকলো সূত্ৰ LaTeX ফৰ্মেটত দিবা**: $formula$ (দুয়োটা $ চিহ্নৰ মাজত)
২. **ধাপে ধাপে সমাধান দেখুৱাবা**
৩. **প্ৰতিটো ধাপৰ ব্যাখ্যা দিবা**
৪. **সহজ পদ্ধতিৰে বুজাবা**
৫. **পৰীক্ষাৰ বাবে গুৰুত্বপূৰ্ণ সূত্ৰবোৰ পৃথকৈ দেখুৱাবা**
৬. **সকলো গাণিতিক সমীকৰণ আৰু সূতৰবোৰ `$` চিহ্নৰ মাজত লিখিবা, আলোচনাৰ বাহিৰত পৃথক লাইনত দেখুৱাবা।**

**গণিতৰ সূত্ৰৰ উদাহৰণ (LaTeX ফৰ্মেটত):**
- দ্বিঘাত সমীকৰণ: $ax^2 + bx + c = 0$
- বৃত্তৰ কালি: $A = \\pi r^2$
- সম্ভাৱিতা: $P(E) = \\frac{{n(E)}}{{n(S)}}$
- পাইথাগোৰাছৰ উপপাদ্য: $a^2 + b^2 = c^2$

**বক্তব্য শৈলী:**
"চিন্তা নকৰিব, এই গণিতৰ সমস্যাটো সহজ।"
"ধাপে ধাপে শিকো আহক..."
"এই সূত্ৰটো মনত ৰাখিব - পৰীক্ষাত আহিব পাৰে!" """,
        
        "guidance": "সমীকৰণ, সূত্ৰ আৰু গাণিতিক প্ৰক্ৰিয়া LaTeX ফৰ্মেটত দেখুৱাব লাগে।"
    },
    
    "🔬 বিজ্ঞান (Science)": {
        "base_prompt": """তুমি এজন বিজ্ঞান শিক্ষক। SEBA দশম শ্ৰেণীৰ বিজ্ঞানৰ {chapter_name} অধ্যায়ৰ সকলো বৈজ্ঞানিক ধাৰণা, প্ৰক্ৰয়া, আৰু নীতি তুমি জানা।

**বিজ্ঞানৰ বিশেষ নিৰ্দেশনা:**
১. **বৈজ্ঞানিক প্ৰক্ৰয়া ধাপে ধাপে বুজাবা**
২. **ৰাসায়নিক সমীকৰণ সঠিকভাৱে দিবা**
৩. **জীৱবিজ্ঞানৰ চিত্ৰ/ৰেখাচিত্ৰৰ বৰ্ণনা দিবা**
৪. **পদাৰ্থবিজ্ঞানৰ সূত্ৰ LaTeX ফৰ্মেটত দিবা**

**ৰাসায়নিক উদাহৰণ:**
$2H_2 + O_2 \\rightarrow 2H_2O$

**পদাৰ্থবিজ্ঞান সূত্ৰ:**
$F = ma$, $v = u + at$

**বক্তব্য শৈলী:**
"এই বৈজ্ঞানিক ধাৰণাটো বুজোৱাৰ বাবে এটা সাধাৰণ উদাহৰণ চাওঁ..."
"প্ৰকতিৰ এই ৰহস্যবোৰ মন কৰিছিল নেকি?" """,
        
        "guidance": "ৰাসায়নিক সমীকৰণ আৰু পদাৰ্থবিজ্ঞানৰ সূত্ৰ LaTeX ফৰ্মেটত দিব লাগে।"
    },
    
    "🌍 সমাজ বিজ্ঞান (Social Science)": {
        "base_prompt": """তুমি এজন সমাজ বিজ্ঞান শিক্ষক। SEBA দশম শ্ৰেণীৰ {chapter_name} অধ্যায়ৰ ঐতিহাসিক ঘটনা, ভৌগোলিক ধাৰণা, অৰ্থনৈতিক নীতি, আৰু ৰাজনৈতিক গঠন তুমি জানা।

**সমাজ বিজ্ঞানৰ বিশেষ নিৰ্দেশনা:**
১. **সহজ অসমীয়া ভাষা ব্যৱহাৰ কৰিবা**
২. **প্ৰশ্ন অনুসৰি উত্তৰ দিবা**""",
        
        "guidance": "তথ্য আৰু বিশ্লেষণ স্পষ্টকৈ দিব লাগে।"
    },
    
    "📖 ইংৰাজী (English)": {
        "base_prompt": """তুমি এজন ইংৰাজী শিক্ষক। SEBA দশম শ্ৰেণীৰ {chapter_name} পাঠটোৰ সকলো সাহিত্যিক উপাদান, ব্যাকৰণ, আৰু ভাষা কৌশল তুমি জানা।

**ইংৰাজীৰ বিশেষ নিৰ্দেশনা:**
১. Answer in English with Assamese translation""",
        
        "guidance": "ইংৰাজী বাক্যৰ সৈতে অসমীয়া ব্যাখ্যা দিব লাগে।"
    },
    
    "📜 অসমীয়া (Assamese)": {
        "base_prompt": """তুমি এজন অসমীয়া সাহিত্য শিক্ষক। SEBA দশম শ্ৰেণীৰ {chapter_name} পাঠটোৰ সাহিত্যিক মুল্য, ভাষা বৈশিষ্ট্য, আৰু সাংস্কৃতিক প্ৰসংগ তুমি জানা।

**অসমীয়াৰ বিশেষ নিৰ্দেশনা:**
১. **সাহিত্যিক বিশ্লেষণ অসমীয়াত দিবা**
২. **প্ৰশ্ন অনুসৰি উত্তৰ দিবা**""",
        
        "guidance": "অসমীয়া ভাষাৰ সৌন্দৰ্য্য আৰু গভীৰতা দেখুৱাব লাগে।"
    },
    
    "📘 হিন্দী (Hindi)": {
        "base_prompt": """तुम एक हिंदी शिक्षक हो। SEBA दशम श्रेणी के {chapter_name} पाठ के सभी साहित्यिक तत्व, व्याकरण, और भाषा कौशल तुम जानते हो।

**हिंदी के विशेष निर्देश:**
१. **साहित्यिक विश्लेषण हिंदी में देना, साथ असमिया व्याख्या देना**
२. **प्रश्न के अनुसार उत्तर देना**""",
        
        "guidance": "हिंदी वाक्य के साथ असमिया व्याख्या देना"
    }
}

# ===============================
# HELPER FUNCTIONS - FIXED CACHE KEY
# ===============================
def create_cache_key(question, subject, chapter_name):
    """Create a unique cache key for the question"""
    # Normalize the question more aggressively for better cache matching
    normalized_question = question.strip().lower()
    
    # Remove extra whitespace
    normalized_question = re.sub(r'\s+', ' ', normalized_question)
    
    # Remove punctuation that might vary
    normalized_question = re.sub(r'[^\w\s\u0980-\u09FF]', '', normalized_question)
    
    normalized_question = normalized_question[:200]
    
    # Normalize subject and chapter
    # Take only the main subject name (before parentheses)
    normalized_subject = subject.split('(')[0].strip() if '(' in subject else subject
    # Take only chapter number/name before colon
    normalized_chapter = chapter_name.split(':')[0].strip() if ':' in chapter_name else chapter_name
    
    key_string = f"{normalized_subject}|{normalized_chapter}|{normalized_question}"
    cache_key = hashlib.md5(key_string.encode()).hexdigest()
    
    return cache_key

def get_question_guidance(question, subject, chapter_name):
    question_lower = question.lower()
    
    simple_keywords = [
        "সংজ্ঞা", "কি", "কাক কয়", "মানে", "definition", "what is", 
        "নাম", "কেইটা", "কিমান", "count", "number", "কি নাম", "কাক বোলে"
    ]
    
    moderate_keywords = [
        "কেনেকৈ", "কেনেকুৱা", "কিয়", "বুজাই দিয়ক", "explain", "how", 
        "why", "difference", "পাৰ্থক্য", "উদাহৰণ", "example", "সমাধান", 
        "solve", "কোনবোৰ", "তুলনা", "compare", "সাদৃশ্য", "similarity"
    ]
    
    complex_keywords = [
        "বিশ্লেষণ", "আলোচনা", "মূল্যায়ন", "বৰ্ণনা", "discuss", 
        "analyze", "evaluate", "describe", "প্ৰমাণ", "prove", 
        "সমাধান কৰি দেখুৱাওক", "solve and show", "step by step",
        "ধাপে ধাপে", "সম্পূৰ্ণ", "সম্পূৰ্ণ বিৱৰণ", "full explanation",
        "সবিশেষ", "in detail", "detailed", "সবিস্তাৰে"
    ]
    
    guidance_text = ""
    
    if "📐 গণিত" in subject:
        guidance_text = "গণিতৰ সমস্যাৰ বাবে ধাপে ধাপে সমাধান দিব লাগে। "
    elif "🔬 বিজ্ঞান" in subject:
        guidance_text = "বিজ্ঞানৰ উত্তৰ বৈজ্ঞানিকভাৱে সঠিক হ'ব লাগে। "
    elif "🌍 সমাজ বিজ্ঞান" in subject:
        guidance_text = "তথ্য সঠিক আৰু বিশ্লেষণাত্মক হ'ব লাগে। "
    
    if any(keyword in question_lower for keyword in complex_keywords):
        return f"{guidance_text} প্ৰশ্নটো জটিল, গতিকে বিশদ উত্তৰ দিবা।"
    elif any(keyword in question_lower for keyword in moderate_keywords):
        return f"{guidance_text} প্ৰশ্নটো মধ্যমীয়া, গতিকে সম্পূৰ্ণ উত্তৰ দিবা।"
    elif any(keyword in question_lower for keyword in simple_keywords):
        return f"{guidance_text} প্ৰশ্নটো সৰল, গতিকে সংক্ষিপ্ত উত্তৰ দিবা।"
    else:
        return f"{guidance_text} প্ৰশ্নৰ প্ৰকৃতি অনুসৰি উত্তৰ দিবা।"

def get_subject_prompt(subject, chapter_name, question):
    if subject not in SUBJECT_PROMPTS:
        subject = "📐 গণিত (Mathematics)"
    
    prompt_template = SUBJECT_PROMPTS[subject]
    base_prompt = prompt_template["base_prompt"].format(chapter_name=chapter_name)
    guidance = prompt_template["guidance"]
    
    if subject == "📐 গণিত (Mathematics)" or subject == "🔬 বিজ্ঞান (Science)":
        latex_instruction = "\n\n**গুৰুত্বপূৰ্ণ**: সকলো গাণিতিক সূত্ৰ, সমীকৰণ LaTeX ফৰ্মেটত দিবা ($ চিহ্নৰ মাজত)।"
    else:
        latex_instruction = ""
    
    question_guidance = get_question_guidance(question, subject, chapter_name)
    
    full_prompt = f"""{base_prompt}

{guidance}{latex_instruction}

**উত্তৰৰ নিৰ্দেশনা:**
{question_guidance}
**উত্তৰ যিমান দৰকাৰী সিমান দীঘল হ'ব লাগে।**

**ছাত্ৰক মাতি লওঁ:**
"বন্ধু, এইটো এনেদৰে বুজিব লাগে..."
"চিন্তা নকৰিব, এইটো সহজ..."

এতিয়া এই প্ৰশ্নটোৰ উত্তৰ দিয়া: {question}"""
    
    return full_prompt

# ===============================
# STREAMLIT STREAMING RESPONSE FUNCTION
# ===============================
def stream_deepseek_response(prompt, question, subject, chapter_name):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "তুমি এজন বিশেষজ্ঞ SEBA দশম শ্ৰেণীৰ শিক্ষক।"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "stream": True
    }
    
    try:
        # Make streaming request
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=payload,
            stream=True,
            timeout=180
        )
        
        if response.status_code == 200:
            full_response = ""
            tokens_used = 0
            
            # Create a placeholder for streaming text
            streaming_placeholder = st.empty()
            
            # Process streaming response
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data = line[6:]  # Remove 'data: ' prefix
                        if data == '[DONE]':
                            break
                        
                        try:
                            chunk = json.loads(data)
                            if 'choices' in chunk and len(chunk['choices']) > 0:
                                delta = chunk['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    full_response += content
                                    
                                    # Update streaming display
                                    streaming_placeholder.markdown(
                                        f"{full_response}<span style='animation: cursor-blink 1s infinite;'>▋</span>",
                                        unsafe_allow_html=True
                                    )
                                
                                # Track tokens
                                if 'usage' in chunk:
                                    tokens_used = chunk['usage'].get('total_tokens', 0)
                        except json.JSONDecodeError:
                            continue
            
            # Clear streaming cursor after completion
            streaming_placeholder.empty()
            
            # Render the final answer
            st.markdown(full_response)
            
            # Store the complete response
            st.session_state.last_answer = full_response
            st.session_state.tokens_used = tokens_used
            
            # Save to cache using manager
            cache_key = create_cache_key(question, subject, chapter_name)
            st.session_state.cache_manager.set(cache_key, {
                'answer': full_response,
                'tokens': tokens_used,
                'subject': subject,
                'chapter': chapter_name,
                'question': question[:200]
            })
            
            # Add to history
            history_entry = {
                'subject': subject,
                'chapter': chapter_name,
                'question': question[:100],
                'timestamp': datetime.now().strftime("%H:%M"),
                'tokens': tokens_used,
                'cached': False
            }
            st.session_state.history.append(history_entry)
            
        else:
            st.error(f"API ত্ৰুটি {response.status_code}: {response.text}")
            
    except Exception as e:
        st.error(f"সংযোগ ত্ৰুটি: {str(e)}")

# ===============================
# INITIALIZE SESSION STATE - FIXED
# ===============================
if 'history' not in st.session_state:
    st.session_state.history = []
if 'current_subject' not in st.session_state:
    st.session_state.current_subject = "📐 গণিত (Mathematics)"
if 'current_chapter' not in st.session_state:
    st.session_state.current_chapter = "অধ্যায় ১"
if 'processing' not in st.session_state:
    st.session_state.processing = False
if 'last_answer' not in st.session_state:
    st.session_state.last_answer = None
if 'question_text' not in st.session_state:
    st.session_state.question_text = ""
if 'streaming_answer' not in st.session_state:
    st.session_state.streaming_answer = ""
if 'tokens_used' not in st.session_state:
    st.session_state.tokens_used = 0
if 'cache_manager' not in st.session_state:
    st.session_state.cache_manager = SupabaseCache(ttl_days=7)
    # Pre-warm cache by checking Supabase connection on startup
    cache_stats = st.session_state.cache_manager.get_stats()
    if cache_stats['supabase_connected'] and cache_stats['supabase_entries'] > 0:
        st.toast(f"📦 Cache loaded: {cache_stats['supabase_entries']} entries available", icon="✅")

if 'show_cached_answer' not in st.session_state:
    st.session_state.show_cached_answer = False
if 'cached_answer_data' not in st.session_state:
    st.session_state.cached_answer_data = None
if 'current_cache_key' not in st.session_state:
    st.session_state.current_cache_key = None
if 'cache_debug' not in st.session_state:
    st.session_state.cache_debug = False

# ===============================
# HEADER SECTION
# ===============================
st.markdown("""
<div class="header-container">
    <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 0.5rem;">
        <div style="font-size: 2rem;">🎓</div>
        <div>
            <h1 class="assamese-title">
                নমস্কাৰ! মই আপোনাৰ দশম শ্ৰেণীৰ AI শিক্ষক
            </h1>
            <p class="assamese-text">
                <span class="assamese-highlight">SEBAৰ সকলো বিষয় মই জানো</span> – গণিত, বিজ্ঞান, সমাজ বিজ্ঞান, ইংৰাজী, অসমীয়া, হিন্দী ইত্যাদি।
            </p>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ===============================
# CONTROL PANEL SECTION
# ===============================
st.markdown('<div class="control-panel">', unsafe_allow_html=True)

control_col1, control_col2 = st.columns(2)
with control_col1:
    st.markdown("#### 📚 বিষয় বাছনি কৰক")
    subject_list = list(SEBA_CURRICULUM.keys())
    current_subject = st.session_state.current_subject
    current_index = subject_list.index(current_subject) if current_subject in subject_list else 0
    
    selected_subject = st.selectbox(
        "আপুনি কোনটো বিষয় শিকিব বিচাৰে?",
        subject_list,
        index=current_index,
        key="subject_selector",
        label_visibility="collapsed"
    )
    
    if selected_subject != st.session_state.current_subject:
        st.session_state.current_subject = selected_subject
        chapters = SEBA_CURRICULUM[selected_subject]
        st.session_state.current_chapter = list(chapters.keys())[0]

with control_col2:
    st.markdown("#### 📖 অধ্যায় বাছনি কৰক")
    chapters = SEBA_CURRICULUM[selected_subject]
    
    chapter_options = []
    chapter_display_map = {}
    for chap_num, chap_name in chapters.items():
        display_text = f"{chap_num}: {chap_name}"
        chapter_options.append(display_text)
        chapter_display_map[display_text] = chap_num
    
    current_chapter = st.session_state.current_chapter
    current_chap_display = next((disp for disp, num in chapter_display_map.items() if num == current_chapter), chapter_options[0])
    current_chap_index = chapter_options.index(current_chap_display) if current_chap_display in chapter_options else 0
    
    selected_chapter_display = st.selectbox(
        "কোন অধ্যায়ৰ পৰা প্ৰশ্ন সুধিব?",
        chapter_options,
        index=current_chap_index,
        key="chapter_selector",
        label_visibility="collapsed"
    )
    
    selected_chapter_key = chapter_display_map[selected_chapter_display]
    if selected_chapter_key != st.session_state.current_chapter:
        st.session_state.current_chapter = selected_chapter_key

st.markdown('</div>', unsafe_allow_html=True)

# ===============================
# CURRENT SELECTION INFO
# ===============================
current_chapter_name = chapters[selected_chapter_key]
st.info(f"""
**📚 বৰ্তমানৰ বিষয়:** {selected_subject}
**📖 বৰ্তমানৰ অধ্যায়:** {current_chapter_name}
""")

# ===============================
# SAMPLE QUESTIONS SECTION
# ===============================
SAMPLE_QUESTIONS = {
    "📐 গণিত (Mathematics)": {
        "অধ্যায় ১": [
            "ইউক্লিডৰ বিভাজন প্ৰমেয়ি (Euclid's Division Lemma) কি? উদাহৰণসহ বুজাই দিয়ক।",
            "অনুৰূপ আৰু মৌলিক সংখ্যাৰ পাৰ্থক্য লিখক। 17 আৰু 23 কি মৌলিক সংখ্যা?",
            "দুটা ধনাত্মক সংখ্যাৰ গ.সা.উ. 24 আৰু ল.সা.গু. 96। সংখ্যাদুটা উলিয়াওক।",
            "প্ৰমাণ কৰক যে √2 এটা অপৰিমেয় সংখ্যা।",
            "15, 18, আৰু 24 ৰ গ.সা.উ. আৰু ল.সা.গু. নিৰ্ণয় কৰক।"
        ],
        "অধ্যায় ২": [
            "বহুপদৰ শূন্যৰ ধাৰণাটো বুজাই দিয়ক। বহুপদ p(x) = x² - 4x + 3 ৰ শূন্যবোৰ উলিয়াওক।",
            "এটা দ্বিঘাত বহুপদ উলিয়াওক যাৰ শূন্যবোৰ 2 আৰু -3।",
            "বহুপদৰ শূন্য আৰু গুণাংকৰ সম্পৰ্ক ব্যাখ্যা কৰক।",
            "বহুপদ x³ - 3x² - x + 3 ৰ শূন্যবোৰ উলিয়াওক।",
            "এটা দ্বিঘাত বহুপদ উলিয়াওক যাৰ শূন্যবোৰৰ যোগফল 4 আৰু গুণফল 3।"
        ],
        "অধ্যায় ৩": [
            "দ্বিঘাত সমীকৰণ x² - 5x + 6 = 0 ৰ মূল নিৰ্ণয় কৰক।",
            "দ্বিঘাত সূত্ৰ ব্যৱহাৰ কৰি 2x² + 5x + 3 = 0 সমীকৰণটো সমাধান কৰক।",
            "দুটা সংখ্যা উলিয়াওক যাৰ যোগফল 27 আৰু গুণফল 182।",
            "দ্বিঘাত সমীকৰণৰ বিচৰ্ষক কাক বোলে? x² - 4x + 4 = 0 ৰ বিচৰ্ষক নিৰ্ণয় কৰক।",
            "এটা আয়তাকাৰ পথাৰৰ দীঘ ইয়াৰ প্ৰস্থতকৈ 5 মিটাৰ বেছি। কালি 150 বৰ্গমিটাৰ হ'লে দীঘ-প্ৰস্থ উলিয়াওক।"
        ],
        "অধ্যায় ৪": [
            "এটা সমান্তৰ শ্ৰেণীৰ প্ৰথম পদ 5 আৰু সাধাৰণ অন্তৰ 3। দশম পদটো উলিয়াওক।",
            "সমান্তৰ শ্ৰেণী 10, 7, 4, ... -62 ৰ শেষৰ পৰা 11 সংখ্যক পদ উলিয়াওক।",
            "সমান্তৰ শ্ৰেণীৰ n সংখ্যক পদৰ যোগফলৰ সূত্ৰটো লিখক।",
            "এটা সমান্তৰ শ্ৰেণীৰ প্ৰথম n পদৰ যোগফল Sn = 3n² + 5n। সাধাৰণ অন্তৰ উলিয়াওক।",
            "100 ৰ পৰা 200 লৈ 6 ৰে বিভাজ্য সংখ্যাবোৰৰ যোগফল উলিয়াওক।"
        ],
        "অধ্যায় ৫": [
            "থেলছৰ উপপাদ্যটো লিখি প্ৰমাণ কৰক।",
            "সমকোণী ত্ৰিভুজ ABC ত A সমকোণ। AD ⟂ BC। প্ৰমাণ কৰক যে AB² = BD × BC।",
            "দুটা সদৃশ ত্ৰিভুজৰ কালিৰ অনুপাত ত্ৰিভুজদুটাৰ অনুৰূপ বাহুৰ অনুপাতৰ বৰ্গৰ সমান - প্ৰমাণ কৰক।",
            "ত্ৰিভুজৰ মধ্যমা ত্ৰিভুজটো সমান কালিৰ দুটা ত্ৰিভুজত বিভক্ত কৰে - প্ৰমাণ কৰক।",
            "পাইথাগোৰাছৰ উপপাদ্যটো প্ৰমাণ কৰক।"
        ],
        "অধ্যায় ৬": [
            "sin²θ + cos²θ = 1 ৰ প্ৰমাণ দিয়ক।",
            "ত্রিকোণমিতিক সূত্র sin(A+B) = sinA cosB + cosA sinB প্ৰমাণ কৰক।",
            "মান নির্ণয় কৰক: sin30° + cos60° - tan45°",
            "যদি sinθ = 3/5 হয়, তেন্তে cosθ আৰু tanθ ৰ মান উলিয়াওক।",
            "প্ৰমাণ কৰক: (1 + tan²θ) = sec²θ"
        ],
        "অধ্যায় ৭": [
            "বৃত্তৰ জ্যাই কেন্দ্ৰত উৎপন্ন কৰা কোণবোৰৰ সম্পৰ্ক কি?",
            "বৃত্তৰ এটা বিন্দুত স্পৰ্শক আৰু ব্যাসাৰ্ধৰ মাজৰ কোণ 90° হয় - প্ৰমাণ কৰক।",
            "বৃত্তচাপে কেন্দ্ৰত উৎপন্ন কৰা কোণ পৰিধিত উৎপন্ন কৰা কোণৰ দুগুণ হয় - প্ৰমাণ কৰক।",
            "দুটা বৃত্ত বাহিৰৰ পৰা স্পৰ্শ কৰিলে স্পৰ্শবিন্দুৰ মাজেৰে যোৱা ৰেখাডাল কেন্দ্ৰদ্বয়ৰ সংযোগী ৰেখাক ছেদ কৰে - প্ৰমাণ কৰক।",
            "বৃত্তৰ ক্ষেত্ৰত বৰ্তুলীয় স্তম্ভৰ উপপাদ্য বুজাই দিয়ক।"
        ],
        "অধ্যায় ৮": [
            "দুটা বিন্দু (2,3) আৰু (5,7) ৰ মাজৰ দূৰত্ব নিৰ্ণয় কৰক।",
            "বিন্দু (4,5), (7,6) আৰু (4,3) ৰ পৰা সমদূৰৱৰ্তী বিন্দুটোৰ স্থানাংক উলিয়াওক।",
            "ভাগ সূত্ৰ ব্যৱহাৰ কৰি বিন্দু (-2,3) আৰু (4,1) ৰ সংযোগী ৰেখাখণ্ডক 3:1 অনুপাতত বিভক্ত কৰা বিন্দুটোৰ স্থানাংক উলিয়াওক।",
            "তিনিটা বিন্দু (1,2), (3,4) আৰু (5,6) একে ৰেখাত আছে নে নাই পৰীক্ষা কৰক।",
            "ত্ৰিভুজৰ মাধ্যমাৰ ছেদবিন্দুৰ স্থানাংকৰ সূত্ৰটো লিখক।"
        ],
        "অধ্যায় ৯": [
            "এটা চুঙাৰ বক্ৰপৃষ্ঠৰ কালি আৰু আয়তনৰ সূত্ৰ লিখক।",
            "এটা শংকুৰ ঢালু উচ্চতা 13 ছে.মি. আৰু ভূমিৰ ব্যাসাৰ্ধ 5 ছে.মি.। ইয়াৰ মুঠ পৃষ্ঠকালি উলিয়াওক।",
            "এটা গোলকৰ আয়তন 4851 ঘন ছে.মি.। ইয়াৰ ব্যাসাৰ্ধ উলিয়াওক।",
            "এটা আয়তক্ষেত্ৰৰ দীঘ 16 মি. আৰু প্ৰস্থ 10 মি.। ইয়াৰ কৰ্ণৰ দৈৰ্ঘ্য উলিয়াওক।",
            "এটা বৰ্গক্ষেত্ৰৰ কৰ্ণৰ দৈৰ্ঘ্য 10√2 ছে.মি.। ইয়াৰ বাহুৰ দৈৰ্ঘ্য উলিয়াওক।"
        ],
        "অধ্যায় ১০": [
            "পৰিসংখ্যাৰ মাধ্যম আৰু মধ্যমাৰ পাৰ্থক্য লিখক।",
            "তলৰ তথ্যৰ পৰা মধ্যমা উলিয়াওক: 12, 15, 18, 20, 25, 30, 32",
            "শ্ৰেণী-বিন্যাসিত তথ্যৰ পৰা বহুলক উলিয়াওকৰ সূত্ৰটো লিখক।",
            "এটা বিভাজনৰ শ্ৰেণী মধ্যবিন্দু 25 আৰু শ্ৰেণী দৈৰ্ঘ্য 10। শ্ৰেণী সীমা উলিয়াওক।",
            "পৰিসংখ্যাৰ চিত্ৰৰ প্ৰয়োজনীয়তা লিখক।"
        ],
        "অধ্যায় ১১": [
            "সম্ভাৱিতা নিৰ্ণয়ৰ মৌলিক সূত্ৰটো লিখক।",
            "এটা মুদ্ৰা দুবাৰ টছ কৰোতে দুয়োবাৰ হেড পোৱাৰ সম্ভাৱিতা কিমান?",
            "52খন তাছপাতৰ পৰা এখন ৰাণী পোৱাৰ সম্ভাৱিতা কিমান?",
            "এটা ডাইচ দলিয়ালে জোৰ সংখ্যা পোৱাৰ সম্ভাৱিতা কিমান?",
            "সম্ভাৱিতা আৰু অনুমানৰ মাজৰ পাৰ্থক্য লিখক।"
        ]
    },
    
    "🔬 বিজ্ঞান (Science)": {
        "অধ্যায় ১": [
            "ৰাসায়নিক বিক্ৰিয়া আৰু ৰাসায়নিক সমীকৰণৰ মাজৰ পাৰ্থক্য কি?",
            "মেগনেছিয়ামৰ ফিটা পোৰাৰ ৰাসায়নিক সমীকৰণ লিখক।",
            "দহন বিক্ৰিয়া কাক বোলে? উদাহৰণ দিয়ক।",
            "বিয়োজন বিক্ৰিয়া কি? উদাহৰণসহ বুজাই দিয়ক।",
            "ৰাসায়নিক সমীকৰণ সন্তুলিত কৰা পদ্ধতি দুটাৰ নাম লিখক।"
        ],
        "অধ্যায় ২": [
            "এছিড আৰু ক্ষাৰকৰ মাজৰ প্ৰধান পাৰ্থক্যবোৰ উল্লেখ কৰক।",
            "ফেনলফথেলিনৰ সৈতে এছিড আৰু ক্ষাৰকৰ বিক্ৰয়া কেনে হয়?",
            "পাকস্থলীত গেছ্ট্ৰিক এছিডৰ পৰিমাণ বাঢ়িলে কি কৰিব লাগে?",
            "কপাৰ চালফেটৰ সৈতে জিংকৰ বিক্ৰয়া দেখুৱাই ৰাসায়নিক সমীকৰণ লিখক।",
            "pH স্কেল কি? ইয়াৰ গুৰুত্ব লিখক।"
        ],
        "অধ্যায় ৩": [
            "ধাতু আৰু অধাতুৰ মাজৰ প্ৰধান পাৰ্থক্যবোৰ উল্লেখ কৰক।",
            "ধাতুবোৰ বিদ্যুৎৰ সুপৰিবাহী কিয়?",
            "ধাতুৰ মলিয়ন কাক বোলে? ইয়াক কেনেকৈ প্ৰতিৰোধ কৰিব পাৰি?",
            "অধাতুৰ প্ৰধান ধৰম্বোৰ লিখক।",
            "লোৰ ওপৰত জিংকৰ প্ৰলেপ দিয়া প্ৰক্ৰিয়াটো ব্যাখ্যা কৰক।"
        ],
        "অধ্যায় ৪": [
            "কাৰ্বনৰ যোজ্য়তা 4 হয় কিয়?",
            "সমসংযোজী বন্ধন কাক বোলে? উদাহৰণ দিয়ক।",
            "হাইড্ৰ'কাৰ্বন কাক বোলে? ইয়াৰ দুটা উদাহৰণ দিয়ক।",
            "সমাবয়ৱী পদাৰ্থ কাক বোলে? উদাহৰণসহ বুজাই দিয়ক।",
            "এলকাইন আৰু এলকিনৰ মাজৰ পাৰ্থক্য লিখক।"
        ],
        "অধ্যায় ৫": [
            "মেন্ডেলিফৰ পৰ্যাবৃত্ত সূত্ৰটো লিখক।",
            "পৰ্যাবৃত্ত সূত্ৰৰ গুৰুত্ব লিখক।",
            "পৰ্যাবৃত্ত তালিকাত আধুনিক দীঘল ৰূপটো ব্যাখ্যা কৰক।",
            "পৰ্যাবৃত্ত তালিকাত পৰ্যায় আৰু শ্ৰেণীৰ ধাৰণা বুজাই দিয়ক।",
            "মৌলৰ যোজ্য়তা পৰ্যাবৃত্ত তালিকাত কিদৰে সলনি হয়?"
        ],
        "অধ্যায় ৬": [
            "মানুহৰ হৃদযন্ত্ৰৰ কাৰ্য প্ৰণালী বৰ্ণনা কৰক।",
            "উচ্চককী আৰু নিম্নককী উদ্ভিদৰ মাজৰ পাৰ্থক্য লিখক।",
            "মানুহৰ ৰেচন প্ৰণালী বৰ্ণনা কৰক।",
            "মানুহৰ শ্বাস-প্ৰশ্বাস প্ৰণালীৰ কাৰ্য ব্যাখ্যা কৰক।",
            "মানুহৰ পাচন প্ৰণালীৰ বিভিন্ন অংশবোৰৰ নাম লিখক।"
        ],
        "অধ্যায় ৭": [
            "নিয়ন্ত্ৰণ আৰু সমন্বয় কাক বোলে?",
            "মানুহৰ মস্তিষ্কৰ তিনিটা অংশৰ নাম লিখি প্ৰত্যেকৰ কাৰ্য বৰ্ণনা কৰক।",
            "প্ৰতিবৰ্তী ক্ৰিয়া কাক বোলে? উদাহৰণ দিয়ক।",
            "হৰম'ন কাক বোলে? ইয়াৰ গুৰুত্ব লিখক।",
            "মানুহৰ স্নায়ু প্ৰণালীৰ গঠন বৰ্ণনা কৰক।"
        ],
        "অধ্যায় ৮": [
            "অলৈঙ্গিক প্ৰজননৰ পদ্ধতিবোৰ উল্লেখ কৰক।",
            "ক্ৰমবিকাশ কাক বোলে? ইয়াৰ গুৰুত্ব লিখক।",
            "স্ত্ৰী আৰু পুৰুষ জননাংগৰ মাজৰ পাৰ্থক্য লিখক।",
            "লিংগিক প্ৰজননৰ সুবিধাবোৰ লিখক।",
            "ভ্রূণ কাক বোলে? ইয়াৰ বিকাশৰ স্তৰবোৰ বৰ্ণনা কৰক।"
        ],
        "অধ্যায় ৯": [
            "ডি.এন.এ.ৰ গঠন বৰ্ণনা কৰক।",
            "বংশগতি আৰু ক্ৰমবিকাশৰ মাজৰ পাৰ্থক্য লিখক।",
            "মেণ্ডেলৰ নিয়মবোৰ ব্যাখ্যা কৰক।",
            "লিংগ নিৰ্ণয় কিহে কৰে? ব্যাখ্যা কৰক।",
            "মিউটেশ্যন কাক বোলে? ইয়াৰ কাৰণবোৰ লিখক।"
        ],
        "অধ্যায় ১০": [
            "প্ৰতিফলন আৰু প্ৰতিসৰণৰ মাজৰ পাৰ্থক্য লিখক।",
            "লেন্ছৰ ক্ষমতাৰ সূত্ৰটো লিখক।",
            "সূৰ্য্যৰ পোহৰ বগা কিয়?",
            "দাপোণৰ সূত্ৰ 1/f = 1/u + 1/v প্ৰমাণ কৰক।",
            "আলোকৰ বিচ্ছুৰণ কাক বোলে? উদাহৰণ দিয়ক।"
        ],
        "অধ্যায় ১১": [
            "মানুহৰ চকুৰ গঠন বৰ্ণনা কৰক।",
            "নিকট দৃষ্টি আৰু দূৰদৃষ্টিৰ পাৰ্থক্য লিখক।",
            "কেমেৰা আৰু চকুৰ মাজৰ সাদৃশ্য লিখক।",
            "ৰামধেনু কেনেকৈ সৃষ্টি হয়?",
            "মায়'পিয়া আৰু হাইপাৰমেট্ৰ'পিয়া ৰোগ কেনেকৈ শুধৰোৱা হয়?"
        ],
        "অধ্যায় ১২": [
            "ওহমৰ সূত্ৰটো লিখি ব্যাখ্যা কৰক।",
            "বিদ্যুৎ প্রবাহ আৰু বিভৱ ভেদৰ মাজৰ সম্পৰ্ক লিখক।",
            "বিদ্যুৎ চুলাৰ কেনেকৈ কাম কৰে?",
            "বৈদ্যুতিক বাল্বৰ ভিতৰত কেনে ধৰণৰ তাঁৰ ব্যৱহাৰ কৰা হয় আৰু কিয়?",
            "বৈদ্যুতিক শক্তি আৰু ক্ষমতাৰ মাজৰ পাৰ্থক্য লিখক।"
        ],
        "অধ্যায় ১৩": [
            "বিদ্যুৎ-চুম্বকীয় প্ৰভাৱ কি?",
            "বিদ্যুৎচুম্বকৰ গঠন আৰু কাৰ্য প্ৰণালী বৰ্ণনা কৰক।",
            "ফেৰাডেৰ ইলেক্ট্ৰ'মেগনেটিক ইণ্ডাকচনৰ নিয়ম লিখক।",
            "মটৰ আৰু জেনেৰেটৰৰ মাজৰ পাৰ্থক্য লিখক।",
            "ট্ৰান্সফৰ্মাৰ কিয় ব্যৱহাৰ কৰা হয়?"
        ],
        "অধ্যায় ১৪": [
            "নৱীকৰণযোগ্য শক্তিৰ উৎসবোৰৰ নাম লিখক।",
            "সৌৰশক্তিৰ সুবিধা আৰু অসুবিধাবোৰ লিখক।",
            "জৈৱ ভৰ কাক বোলে? ইয়াৰ গুৰুত্ব লিখক।",
            "ভূ-তাপীয় শক্তিৰ উৎস লিখক।",
            "নিউক্লীয় বিভাজন আৰু নিউক্লীয় সংযোজনৰ মাজৰ পাৰ্থক্য লিখক।"
        ],
        "অধ্যায় ১৫": [
            "পৰিৱেশ দূষণৰ কাৰণবোৰ উল্লেখ কৰক।",
            "এছিড বৰষুণ কিয় হয়? ইয়াৰ প্ৰভাৱ লিখক।",
            "ওজন স্তৰৰ ক্ষতিৰ কাৰণবোৰ লিখক।",
            "জৈৱবৈচিত্ৰ্যৰ গুৰুত্ব লিখক।",
            "হৰিত গৃহ প্ৰভাৱ কি? ইয়াৰ পৰিণতি লিখক।"
        ],
        "অধ্যায় ১৬": [
            "প্ৰাকৃতিক সম্পদ সংৰক্ষণৰ উপায়বোৰ লিখক।",
            "বৰ্ষাৰণ্য সংৰক্ষণৰ গুৰুত্ব লিখক।",
            "জলসম্পদৰ ব্যৱস্থাপনা কেনেকৈ কৰিব লাগে?",
            "মৃত্তিকা সংৰক্ষণৰ পদ্ধতিবোৰ লিখক।",
            "বায়ু দূষণ ৰোধ কৰাৰ উপায়বোৰ লিখক।"
        ]
    },
    
    "🌍 সমাজ বিজ্ঞান (Social Science)": {
        "অধ্যায় ১": [
            "ইউৰোপত ৰাষ্ট্ৰবাদৰ উত্থানৰ প্ৰধান কাৰকবোৰ কি আছিল?",
            "ইটালীৰ ঐক্যবাদত গেৰিবাল্ডিৰ ভূমিকা আলোচনা কৰক।",
            "বিসমাৰ্কৰ ৰক্ত আৰু লোহাৰ নীতি ব্যাখ্যা কৰক。",
            "জাৰ্মানীৰ ঐক্যবাদ কেনেকৈ সম্পন্ন হৈছিল?",
            "ৰাষ্ট্ৰবাদৰ উত্থানে ইউৰোপত কেনে প্ৰভাৱ পেলাইছিল?"
        ],
        "অধ্যায় ২": [
            "ভাৰতীয় জাতীয়তাবাদৰ উত্থানত মহাত্মা গান্ধীৰ অৱদান আলোচনা কৰক।",
            "ভাৰতীয় জাতীয় কংগ্ৰেছৰ প্ৰতিষ্ঠা আৰু ইয়াৰ প্ৰাথমিক লক্ষ্যবোৰ লিখক।",
            "বংগ বিভাজনৰ কাৰণ আৰু প্ৰভাৱ আলোচনা কৰক。",
            "স্বদেশী আন্দোলন কি আছিল? ইয়াৰ গুৰুত্ব লিখক।",
            "জালিয়ানৱালাবাগ হত্যাকাণ্ডৰ ঘটনাটো বৰ্ণনা কৰক।"
        ],
        "অধ্যায় ৩": [
            "ভূগোলৰ প্ৰাকৃতিক আৰু মানৱ সম্পদৰ পাৰ্থক্য দৰ্শোৱা।",
            "অসমৰ প্ৰাকৃতিক সম্পদবোৰৰ নাম লিখক।",
            "ভাৰতৰ কৃষিজ সম্পদবোৰৰ নাম লিখক。",
            "খনিজ সম্পদৰ গুৰুত্ব লিখক।",
            "বনজ সম্পদ সংৰক্ষণৰ গুৰুত্ব লিখক।"
        ],
        "অধ্যায় ৪": [
            "অৰ্থনৈতিক উন্নয়ন আৰু অৰ্থনৈতিক বৃদ্ধিৰ মাজৰ পাৰ্থক্য লিখক।",
            "ভাৰতৰ অৰ্থনৈতিক উন্নয়নত কৃষিৰ ভূমিকা আলোচনা কৰক。",
            "শিল্পায়নৰ সুবিধা আৰু অসুবিধাবোৰ লিখক।",
            "বেকাৰ সমস্যা সমাধানৰ উপায়বোৰ লিখক।",
            "দৰিদ্ৰতা নিৰ্মূল কৰাৰ উপায়বোৰ আলোচনা কৰক。"
        ],
        "অধ্যায় ৫": [
            "অসমৰ লোক সংস্কৃতিৰ বৈশিষ্ট্যসমূহ বৰ্ণনা কৰক।",
            "বিহুৰ বিভিন্ন ৰূপবোৰৰ বৰ্ণনা দিয়ক।",
            "অসমীয়া লোক সংগীতৰ বৈশিষ্ট্য লিখক।",
            "অসমৰ লোক নৃত্যৰ নাম লিখি বৰ্ণনা কৰক。",
            "অসমৰ সাজ-পোচাকৰ বৈচিত্ৰ্য বৰ্ণনা কৰক。"
        ],
        "অধ্যায় ৬": [
            "ভাৰতৰ প্ৰধান উদ্যোগবোৰৰ নাম লিখক。",
            "লো আৰু ইস্পাত উদ্যোগৰ গুৰুত্ব লিখক。",
            "কপাহী বস্ত্ৰ উদ্যোগৰ সমস্যাসমূহ আলোচনা কৰক。",
            "ছুগাৰ মিল উদ্যোগৰ স্থানীয়কৰণৰ কাৰণবোৰ লিখক。",
            "উদ্যোগিক দূষণ ৰোধ কৰাৰ উপায়বোৰ লিখক。"
        ],
        "অধ্যায় ৭": [
            "ভাৰতীয় অৰ্থনীতিৰ প্ৰধান সমস্যাসমূহ আলোচনা কৰক。",
            "মুদ্ৰাস্ফীতিৰ কাৰণ আৰু প্ৰভাৱ লিখক。",
            "বিত্তীয় ঘাটিৰ অৰ্থ লিখক。",
            "ৰপ্তানি আৰু আমদানিৰ মাজৰ পাৰ্থক্য লিখক。",
            "অৰ্থনৈতিক আয়োজন কেনেকৈ কৰা হয়?"
        ],
        "অধ্যায় ৮": [
            "ভাৰতৰ ৰাজনৈতিক দলসমূহৰ শ্ৰেণীবিভাজন কৰক।",
            "ৰাষ্ট্ৰীয় দল আৰু ৰাজ্যিক দলৰ মাজৰ পাৰ্থক্য লিখক。",
            "ভাৰতত বহুদলীয় গণতন্ত্ৰৰ গুৰুত্ব লিখক।",
            "ৰাজনৈতিক দলৰ কাৰ্যবোৰ লিখক।",
            "নিৰ্বাচন আয়োগৰ কাৰ্যবোৰ লিখক。"
        ],
        "অধ্যায় ৯": [
            "ভাৰতৰ সংবিধানত ক্ষমতাৰ বিভাজন কেনেদৰে কৰা হৈছে?",
            "কাৰ্যপালিকা, বিধানমণ্ডল আৰু ন্যায়পালিকাৰ মাজৰ সম্পৰ্ক লিখক।",
            "কেন্দ্ৰ আৰু ৰাজ্য চৰকাৰৰ মাজৰ সম্পৰ্ক লিখক।",
            "স্থানীয় স্বায়ত্তশাসনৰ গুৰুত্ব লিখক।",
            "পঞ্চায়েতী ৰাজ ব্যৱস্থাৰ গঠন বৰ্ণনা কৰক।"
        ],
        "অধ্যায় ১০": [
            "জনসম্পদ উন্নয়নৰ অৰ্থ লিখক।",
            "শিক্ষাৰ গুৰুত্ব লিখক。",
            "স্বাস্থ্য সেৱাৰ উন্নয়নৰ উপায়বোৰ লিখক।",
            "জনসংখ্যা বিস্ফোৰণৰ কাৰণবোৰ লিখক।",
            "লিংগ সমতাৰ গুৰুত্ব লিখক।"
        ]
    },
    
    "📖 ইংৰাজী (English)": {
        "পাঠ ১": [
            "What is the central theme of 'A Letter to God'?",
            "Describe the character of Lencho in the story.",
            "Why did Lencho write a letter to God?",
            "What does the story teach us about faith and human nature?",
            "How did the postmaster react to Lencho's letter?"
        ],
        "পাঠ ২": [
            "Describe the qualities of Nelson Mandela that made him a great leader.",
            "What is the significance of the title 'Long Walk to Freedom'?",
            "What were Mandela's views on love and hate?",
            "Describe the inauguration ceremony at the Union Buildings.",
            "What does Mandela say about courage?"
        ],
        "পাঠ ৩": [
            "What is the moral lesson of 'Two Stories about Flying'?",
            "Compare and contrast the two stories in this lesson.",
            "Describe the young seagull's first flight.",
            "What motivated the young seagull to finally fly?",
            "How does the second story about the pilot differ from the first?"
        ],
        "পাঠ ৪": [
            "How does Anne Frank's diary reflect the struggles of Jewish people during WWII?",
            "What kind of person was Anne Frank? Describe her character.",
            "Why is Anne's diary considered an important historical document?",
            "What were Anne's dreams and aspirations?",
            "How did Anne view her captivity in the Secret Annex?"
        ],
        "পাঠ ৫": [
            "What is the significance of the hundred dresses in the story?",
            "Describe the character of Wanda Petronski.",
            "Why did the other girls make fun of Wanda?",
            "What lesson did Maddie learn from the incident?",
            "How does the story address the theme of bullying?"
        ],
        "পাঠ ৬": [
            "How does Maddie's character develop in 'The Hundred Dresses II'?",
            "What did the girls discover about Wanda after she left?",
            "Why did Maddie feel guilty about her behavior?",
            "What was Wanda's letter about?",
            "How did the story end?"
        ],
        "পাঠ ৭": [
            "Describe the cultural diversity of India as shown in 'Glimpses of India'.",
            "What are the main features of Coorg as described in the text?",
            "How is tea cultivation described in the lesson?",
            "What makes Goa different from other parts of India?",
            "What are the various glimpses of India presented in this lesson?"
        ],
        "পাঠ ৮": [
            "What is the relationship between the narrator and Mijbil in 'Mijbil the Otter'?",
            "Describe Mijbil's habits and characteristics.",
            "How did the otter adjust to his new environment?",
            "What adventures did the narrator have with Mijbil?",
            "What does the story tell us about human-animal relationships?"
        ],
        "পাঠ ৯": [
            "What does Valli learn from her bus journey in 'Madam Rides the Bus'?",
            "Describe Valli's character and her curiosity.",
            "What were Valli's preparations for her bus journey?",
            "What did Valli see during her journey?",
            "How did the journey change Valli?"
        ],
        "পাঠ ১০": [
            "What is the main teaching of Buddha in 'The Sermon at Benares'?",
            "How did Kisa Gotami realize the truth about death?",
            "What does Buddha say about grief and suffering?",
            "Why is death compared to ripe fruits?",
            "What is the significance of the mustard seed in the story?"
        ],
        "পাঠ ১১": [
            "Describe the humorous elements in 'The Proposal'.",
            "What is the main conflict in the play?",
            "Describe the characters of Lomov, Natalya, and Chubukov.",
            "What are they arguing about in the play?",
            "How does the play end?"
        ]
    },
    
    "📜 অসমীয়া (Assamese)": {
        "পাঠ ১": [
            "বৰগীতৰ সাহিত্যিক মূল্য আলোচনা কৰক।",
            "শংকৰদেৱে ৰচনা কৰা বৰগীতৰ বিষয়বস্তু কি?",
            "বৰগীতৰ ভাষা শৈলীৰ বৈশিষ্ট্য লিখক।",
            "বৰগীতত প্ৰকাশ পোৱা ভক্তিধর্মীয় ভাৱ লিখক।",
            "বৰগীতৰ ৰচনা ৰীতি ব্যাখ্যা কৰক।"
        ],
        "পাঠ ২": [
            "জীৱন-সঙ্গীত কবিতাটোৰ মূল বক্তব্য ব্যাখ্যা কৰক।",
            "জীৱন-সঙ্গীত কবিতাটোত কবিয়ে জীৱনক কেনেদৰে চিত্ৰিত কৰিছে?",
            "কবিতাটোৰ ছন্দ আৰু অলংকাৰৰ বৈশিষ্ট্য লিখক।",
            "কবিতাটোত প্ৰকাশ পোৱা দাৰ্শনিক চিন্তা আলোচনা কৰক।",
            "জীৱন-সঙ্গীত কবিতাটোৰ শিৰোনামৰ সাৰ্থকতা লিখক।"
        ],
        "পাঠ ৩": [
            "প্ৰশস্তি কবিতাটোত কবিয়ে কি বৰ্ণনা কৰিছে?",
            "প্ৰশস্তি কবিতাটোৰ ৰচনা শৈলীৰ বৈশিষ্ট্য লিখক।",
            "কবিতাটোত ব্যৱহৃত উপমা আৰু ৰূপকবোৰ উল্লেখ কৰক।",
            "প্ৰশস্তি কবিতাটোৰ ভাষাৰ সৌন্দৰ্য্য বৰ্ণনা কৰক।",
            "কবিতাটোৰ প্ৰাসঙ্গিকতা বৰ্তমান সময়ত আলোচনা কৰক।"
        ],
        "পাঠ ৪": [
            "মোৰ মৰমি জনমভূমি কবিতাটোৰ বিষয়বস্তু আলোচনা কৰক।",
            "কবিতাটোত কবিয়ে মাতৃভূমিৰ প্ৰতি থকা মৰম কেনেদৰে প্ৰকাশ কৰিছে?",
            "মোৰ মৰমি জনমভূমি কবিতাটোৰ শৈলীগত বৈশিষ্ট্য লিখক।",
            "কবিতাটোত প্ৰকাশ পোৱা দেশপ্ৰেমৰ ভাৱ লিখক।",
            "কবিতাটোৰ শিৰোনামৰ সাৰ্থকতা লিখক।"
        ],
        "পাঠ ৫": [
            "অসমীয়া ভাষাৰ উন্নতিৰ বাবে কি কৰিব লাগে?",
            "অসমীয়া ভাষাৰ বৰ্তমান অৱস্থা আলোচনা কৰক।",
            "ভাষা সংৰক্ষণৰ গুৰুত্ব লিখক।",
            "অসমীয়া ভাষাৰ উন্নতিত শিক্ষাৰ ভূমিকা লিখক।",
            "ভাষা বিকাশৰ বাবে আধুনিক প্ৰযুক্তিৰ ভূমিকা আলোচনা কৰক。"
        ],
        "পাঠ ৬": [
            "অসমৰ লোক-সংস্কৃতিৰ বৈশিষ্ট্যসমূহ বৰ্ণনা কৰক।",
            "অসমৰ লোক-সংগীতৰ প্ৰকাৰবোৰৰ নাম লিখক।",
            "অসমৰ লোক-নৃত্যৰ বৈচিত্ৰ্য বৰ্ণনা কৰক।",
            "অসমীয়া লোক-কথাৰ বৈশিষ্ট্য লিখক।",
            "লোক-সংস্কৃতি সংৰক্ষণৰ গুৰুত্ব লিখক।"
        ],
        "পাঠ ৭": [
            "আমাৰ ঋতু কবিতাটোত কবিয়ে ঋতুচক্ৰ কেনেদৰে বৰ্ণনা কৰিছে?",
            "অসমৰ ছয়টা ঋতুৰ নাম লিখি প্ৰত্যেকৰ বৈশিষ্ট্য বৰ্ণনা কৰক।",
            "ঋতুভিত্তিক কৃষিকৰ্মৰ সম্পৰ্ক লিখক।",
            "ঋতু পৰিৱৰ্তনে মানুহৰ জীৱনত কেনে প্ৰভাৱ পেলায়?",
            "কবিতাটোত ব্যৱহৃত প্ৰাকৃতিক দৃশ্যবোৰ বৰ্ণনা কৰক।"
        ],
        "পাঠ ৮": [
            "বহাগ বিহুৰ সামাজিক আৰু সাংস্কৃতিক গুৰুত্ব লিখক।",
            "বহাগ বিহু উদযাপনৰ পৰম্পৰাগত ৰীতি-নীতিবোৰ বৰ্ণনা কৰক।",
            "বিহু গীতৰ বিষয়বস্তু আৰু বৈশিষ্ট্য লিখক।",
            "বিহু নৃত্যৰ বিভিন্ন ৰূপবোৰৰ বৰ্ণনা দিয়ক।",
            "বিহুৰ ঐতিহ্য সংৰক্ষণৰ গুৰুত্ব লিখক।"
        ],
        "পাঠ ৯": [
            "মহাপুৰুষীয়া ধৰ্মৰ মূল নীতিবোৰ কি?",
            "শংকৰদেৱ আৰু মাধৱদেৱৰ ধৰ্মীয় অৱদান আলোচনা কৰক।",
            "মহাপুৰুষীয়া ধৰ্মত নাম-ধৰ্মৰ গুৰুত্ব লিখক।",
            "একশৰণ ধৰ্মৰ মূল তত্ত্ববোৰ ব্যাখ্যা কৰক।",
            "মহাপুৰুষীয়া ধৰ্মৰ প্ৰচাৰৰ বাবে কি কৰা হৈছিল?"
        ],
        "পাঠ ১০": [
            "সাহিত্যৰ ৰূপ পাঠটোত সাহিত্যৰ কেইটা ৰূপৰ কথা উল্লেখ আছে?",
            "সাহিত্যৰ বিভিন্ন ৰূপবোৰৰ নাম লিখি বৰ্ণনা কৰক।",
            "কবিতা আৰু গদ্যৰ মাজৰ পাৰ্থক্য লিখক।",
            "নাটকৰ বৈশিষ্ট্যবোৰ লিখক。",
            "সাহিত্যৰ সমাজত থকা ভূমিকা আলোচনা কৰক।"
        ]
    },
    
    "📘 হিন্দী (Hindi)": {
        "পাঠ ১": [
            "साखी पाठ का मुख्य संदेश क्या है?",
            "कबीरदास की साखियों की भाषा-शैली पर प्रकाश डालिए।",
            "साखी पाठ की किन्हीं दो साखियों का अर्थ समझाइए।",
            "कबीरदास के दोहे समाज को क्या संदेश देते हैं?",
            "साखी पाठ से हमें क्या शिक्षा मिलती है?"
        ],
        "পাঠ ২": [
            "पद पाठ की साहित्यिक विशेषताएँ बताइए।",
            "मीराबाई के पदों में भक्ति भावना कैसे व्यक्त हुई है?",
            "मीराबाई के जीवन पर प्रकाश डालिए।",
            "पद पाठ की किन्हीं दो पंक्तियों का भावार्थ लिखिए।",
            "मीराबाई के पदों में कृष्ण भक्ति कैसे दिखाई देती है?"
        ],
        "পাঠ ৩": [
            "दोहे पाठ के दोहे का अर्थ समझाइए।",
            "रहीम के दोहों की विशेषताएँ बताइए।",
            "रहीम के जीवन पर संक्षिप्त टिप्पणी लिखिए।",
            "दोहे पाठ के किन्हीं दो दोहों का भावार्थ लिखिए।",
            "रहीम के दोहे हमें क्या सीख देते हैं?"
        ],
        "পাঠ ৪": [
            "मनुष्यता कविता का सारांश लिखिए।",
            "मैथिलीशरण गुप्त की 'मनुष्यता' कविता का मूल भाव क्या है?",
            "मनुष्यता कविता की भाषा-शैली पर प्रकाश डालिए।",
            "कविता में मनुष्य के कर्तव्यों के बारे में क्या कहा गया है?",
            "मनुष्यता कविता से हमें क्या प्रेरणा मिलती है?"
        ],
        "পাঠ ৫": [
            "पर्वत प्रदेश में पावस कविता की भाषा-शैली पर प्रकाश डालिए।",
            "सुमित्रानंदन पंत की कविता 'पर्वत प्रदेश में पावस' का केंद्रीय भाव लिखिए।",
            "कविता में वर्षा ऋतु का कैसा चित्रण किया गया है?",
            "कविता में प्रकृति चित्रण कैसे हुआ है?",
            "पर्वत प्रदेश में पावस कविता की किन्हीं दो पंक्तियों की व्याख्या कीजिए।"
        ],
        "পাঠ ৬": [
            "मधुर-मधुर मेरे दीपक जल कविता की व्याख्या कीजिए।",
            "महादेवी वर्मा की कविता 'मधुर-मधुर मेरे दीपक जल' का सार लिखिए।",
            "कविता में दीपक किसका प्रतीक है?",
            "महादेवी वर्मा की काव्य शैली की विशेषताएँ बताइए।",
            "कविता से हमें क्या संदेश मिलता है?"
        ],
        "পাঠ ৭": [
            "तोप कविता का प्रतीकार्थ समझाइए।",
            "केदारनाथ अग्रवाल की कविता 'तोप' का मुख्य विषय क्या है?",
            "कविता में तोप किसका प्रतीक है?",
            "कविता में युद्ध के प्रति क्या दृष्टिकोण व्यक्त किया गया है?",
            "तोप कविता की भाषागत विशेषताएँ लिखिए।"
        ],
        "পাঠ ৮": [
            "कर चले हम फ़िदा गीत का ऐतिहासिक संदर्भ क्या है?",
            "गीत 'कर चले हम फ़िदा' का मुख्य भाव लिखिए।",
            "यह गीत हमें देशभक्ति की क्या सीख देता है?",
            "गीत में वीर सैनिकों के बलिदान का कैसे वर्णन किया गया है?",
            "गीत की भाषा-शैली पर टिप्पणी लिखिए।"
        ],
        "পাঠ ৯": [
            "आत्मत्राण कविता का केंद्रीय भाव लिखिए।",
            "रवींद्रनाथ टैगोर की कविता 'आत्मत्राण' का सारांश लिखिए।",
            "कविता में कवि ने ईश्वर से क्या प्रार्थना की है?",
            "आत्मत्राण कविता से हमें क्या शिक्षा मिलती है?",
            "कविता की भाषागत विशेषताएँ बताइए।"
        ],
        "পাঠ ১০": [
            "बड़े भाई साहब कहानी का नैतिक संदेश क्या है?",
            "प्रेमचंद की कहानी 'बड़े भाई साहब' का सारांश लिखिए।",
            "कहानी के दोनों भाइयों के चरित्र की तुलना कीजिए।",
            "कहानी में शिक्षा प्रणाली पर क्या टिप्पणी की गई है?",
            "प्रेमचंद की कहानी शैली की विशेषताएँ बताइए।"
        ]
    }
}

# ===============================
# STYLED DROPDOWN SELECTOR
# ===============================
st.markdown("""
<div style="background: linear-gradient(145deg, #f8f9fa 0%, #e3f2fd 100%);
            padding: 0.8rem;
            border-radius: 10px;
            border-left: 4px solid #2196F3;
            margin-bottom: 1rem;">
    <h4 style="color: #0d47a1; margin: 0; display: flex; align-items: center; gap: 0.5rem;">
        <span style="font-size: 1.2rem;">📋</span> নমুনা প্ৰশ্ন বাছনি কৰক
    </h4>
    <p style="color: #546e7a; font-size: 0.85rem; margin: 0.3rem 0 0 0;">
        তলৰ ড্ৰপডাউনৰ পৰা এটা প্ৰশ্ন বাছনি কৰক
    </p>
</div>
""", unsafe_allow_html=True)

sample_questions = SAMPLE_QUESTIONS.get(selected_subject, {}).get(selected_chapter_key, [])

if sample_questions:
    # Create dropdown options with icons for better visual
    options = ["🎯 এটা প্ৰশ্ন বাছনি কৰক"] + sample_questions
    
    # Custom styled dropdown container
    st.markdown("""
    <div style="background: white; 
                border: 2px solid #e3f2fd; 
                border-radius: 8px; 
                padding: 1rem;
                margin-bottom: 1rem;
                box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
    """, unsafe_allow_html=True)
    
    selected_question = st.selectbox(
        "**নমুনা প্ৰশ্নৰ তালিকা:**",
        options=options,
        index=0,
        key="styled_dropdown",
        help="ড্ৰপডাউন খুলি প্ৰশ্নবোৰ চাওক",
        label_visibility="collapsed"
    )
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # If a question is selected
    if selected_question != "🎯 এটা প্ৰশ্ন বাছনি কৰক":
        # Show selected question in a styled box
        st.markdown(f"""
        <div style="background: linear-gradient(145deg, #e8f5e9 0%, #f1f8e9 100%);
                    border-left: 4px solid #4CAF50;
                    border-radius: 8px;
                    padding: 1rem;
                    margin: 1rem 0;
                    box-shadow: 0 3px 10px rgba(76, 175, 80, 0.1);">
            <div style="display: flex; align-items: center; margin-bottom: 0.5rem;">
                <div style="background: #4CAF50; 
                            color: white; 
                            width: 32px; 
                            height: 32px; 
                            border-radius: 50%; 
                            display: flex; 
                            align-items: center; 
                            justify-content: center; 
                            font-size: 1rem; 
                            margin-right: 0.8rem;">
                    ✓
                </div>
                <div>
                    <div style="font-weight: 700; color: #2e7d32; font-size: 0.9rem;">
                        বাছনি কৰা প্ৰশ্ন
                    </div>
                    <div style="font-size: 0.8rem; color: #558b2f;">
                        এতিয়া এই প্ৰশ্নটো ব্যৱহাৰ কৰিব পাৰে
                    </div>
                </div>
            </div>
            <div style="background: white; 
                        padding: 1rem; 
                        border-radius: 6px; 
                        border: 1px solid #c8e6c9;
                        font-size: 0.95rem;
                        color: #333;
                        line-height: 1.5;">
                {selected_question}
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Styled load button
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button(
                "✅ এই প্ৰশ্নটো ব্যৱহাৰ কৰক", 
                use_container_width=True,
                type="primary",
                help="প্ৰশ্নটো মেইন ইনপুট বাক্সত ল'ড কৰিব"
            ):
                st.session_state.question_text = selected_question
                st.success("✅ প্ৰশ্নটো সফলভাৱে ল'ড কৰা হৈছে!")
                st.rerun()
        
        with col2:
            if st.button(
                "🔄 নতুনকৈ বাছনি কৰক", 
                use_container_width=True,
                type="secondary",
                help="বেলেগ প্ৰশ্ন বাছনি কৰিব"
            ):
                # Reset dropdown by removing the key
                if 'styled_dropdown' in st.session_state:
                    del st.session_state.styled_dropdown
                st.rerun()
    
    # Show quick stats
    st.markdown(f"""
    <div style="display: flex; 
                justify-content: space-between; 
                background: #f5f5f5; 
                padding: 0.6rem 1rem; 
                border-radius: 6px; 
                margin-top: 1rem;
                font-size: 0.85rem;">
        <div style="color: #666;">
            <span style="font-weight: bold; color: #2196F3;">{len(sample_questions)}</span> টা প্ৰশ্ন উপলব্ধ
        </div>
        <div style="color: #666;">
            বিষয়: <span style="font-weight: bold; color: #2196F3;">{selected_subject.split(' ')[1] if ' ' in selected_subject else selected_subject}</span>
        </div>
        <div style="color: #666;">
            অধ্যায়: <span style="font-weight: bold; color: #2196F3;">{selected_chapter_key}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

else:
    st.markdown("""
    <div style="background: linear-gradient(145deg, #fff3e0 0%, #ffecb3 100%);
                border-left: 4px solid #FF9800;
                border-radius: 8px;
                padding: 1.5rem;
                text-align: center;
                margin: 1rem 0;">
        <div style="font-size: 3rem; margin-bottom: 0.5rem;">📭</div>
        <h4 style="color: #EF6C00; margin: 0 0 0.5rem 0;">নমুনা প্ৰশ্ন উপলব্ধ নাই</h4>
        <p style="color: #8d6e63; margin: 0; font-size: 0.9rem;">
            <strong>{selected_subject}</strong>ৰ <strong>{current_chapter_name}</strong> অধ্যায়ৰ বাবে 
            নমুনা প্ৰশ্ন যোগ কৰা হোৱা নাই। <br>আপুনি নিজৰ প্ৰশ্নটো ওপৰৰ বাক্সত লিখিব পাৰে।
        </p>
    </div>
    """, unsafe_allow_html=True)

# Add custom CSS for better dropdown styling
st.markdown("""
<style>
/* Style the selectbox container */
div[data-baseweb="select"] {
    border-radius: 6px !important;
}

/* Style the dropdown arrow */
div[data-baseweb="select"] > div > div > svg {
    color: #2196F3 !important;
}

/* Style the selected value */
div[data-baseweb="select"] > div > div {
    background-color: #f8fdff !important;
    border: 2px solid #bbdefb !important;
    border-radius: 6px !important;
    color: #1565c0 !important;
    font-weight: 500 !important;
}

/* Style dropdown options */
div[role="listbox"] div {
    padding: 0.5rem 1rem !important;
    border-bottom: 1px solid #f0f0f0 !important;
}

div[role="listbox"] div:hover {
    background-color: #e3f2fd !important;
    color: #0d47a1 !important;
}

/* First option (placeholder) styling */
div[role="listbox"] div:first-child {
    color: #78909c !important;
    font-style: italic !important;
}

/* Style the primary button */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #4CAF50 0%, #2E7D32 100%) !important;
    border: none !important;
    font-weight: 600 !important;
}

.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #66BB6A 0%, #388E3C 100%) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 3px 8px rgba(76, 175, 80, 0.3) !important;
}

/* Style the secondary button */
.stButton > button[kind="secondary"] {
    background: linear-gradient(145deg, #ffffff 0%, #f5f5f5 100%) !important;
    border: 2px solid #e0e0e0 !important;
    color: #666 !important;
    font-weight: 500 !important;
}

.stButton > button[kind="secondary"]:hover {
    background: linear-gradient(145deg, #f5f5f5 0%, #eeeeee 100%) !important;
    border-color: #bdbdbd !important;
    color: #424242 !important;
}
</style>
""", unsafe_allow_html=True)

# ===============================
# QUESTION INPUT AREA
# ===============================
st.markdown("---")
st.markdown("#### ✍️ আপোনাৰ প্ৰশ্নটো ইয়াত লিখক")

question = st.text_area(
    "আপোনাৰ প্ৰশ্নটো ইয়াত লিখক:",
    value=st.session_state.question_text,
    height=100,
    placeholder=f"উদাহৰণ: '{current_chapter_name}' অধ্যায়টো মোৰ বাবে বুজাই দিয়ক...",
    key="question_input",
    label_visibility="collapsed"
)

if question != st.session_state.question_text:
    st.session_state.question_text = question

# Show API key status
if not api_key:
    st.error("""
    ⚠️ **API কি ছেট আপ কৰক:**
    
    **Hugging Face Spaces:**
    ১. Space Settings → Repository secrets
    ২. `DEEPSEEK_API_KEY` যোগ কৰক
    ৩. আপোনাৰ DeepSeek API কি দিয়ক
    
    **স্থানীয়ভাবে:**
    ```bash
    export DEEPSEEK_API_KEY="your-api-key-here"
    ```
    """)

# ===============================
# CACHE CHECK AND SUBMIT BUTTON - FIXED VERSION
# ===============================
submit_disabled = not (question.strip() and api_key)
col1, col2, col3 = st.columns([1, 2, 1])

with col2:
    if st.button(
        "🚀 উত্তৰ দিবলৈ দিয়ক!", 
        type="primary", 
        use_container_width=True,
        disabled=submit_disabled
    ):
        if not question.strip():
            st.error("❌ অনুগ্ৰহ কৰি প্ৰশ্নটো লিখক!")
        elif not api_key:
            st.error("❌ API কি ছেট আপ কৰক!")
        else:
            # Check cache first
            cache_key = create_cache_key(question, selected_subject, current_chapter_name)
            
            # Get cache stats for debugging
            cache_stats = st.session_state.cache_manager.get_stats()
            
            # Check if we should show debug info
            if cache_stats['supabase_connected']:
                st.toast(f"🔍 Checking Supabase cache ({cache_stats['supabase_entries']} entries)", icon="🔍")
            
            cached_entry = st.session_state.cache_manager.get(cache_key)
            
            if cached_entry:
                # Determine cache source
                cache_source = "Memory" if cache_key in st.session_state.cache_manager.memory_cache else "Supabase"
                st.toast(f"🎯 Cache hit from {cache_source}!", icon="⚡")
                
                # Load from cache
                st.session_state.last_answer = cached_entry['answer']
                st.session_state.tokens_used = cached_entry['tokens']
                
                # Add to history with cache flag
                history_entry = {
                    'subject': selected_subject,
                    'chapter': current_chapter_name,
                    'question': question[:100],
                    'timestamp': datetime.now().strftime("%H:%M"),
                    'tokens': cached_entry['tokens'],
                    'cached': True,
                    'cache_source': cache_source
                }
                st.session_state.history.append(history_entry)
                
                # Show cached answer
                st.session_state.show_cached_answer = True
                st.session_state.cached_answer_data = cached_entry
                st.session_state.current_cache_key = cache_key
                st.session_state.processing = False
            else:
                # Cache miss
                if cache_stats['supabase_connected']:
                    st.toast("❌ Cache miss - calling API...", icon="🤖")
                else:
                    st.toast("🤖 Calling DeepSeek API...", icon="🤖")
                
                # Not in cache, proceed with API call
                st.session_state.processing = True
                st.session_state.current_cache_key = cache_key

# ===============================
# DISPLAY CACHED ANSWER - FIXED VERSION
# ===============================
if st.session_state.get('show_cached_answer') and st.session_state.get('cached_answer_data'):
    st.markdown("---")
    
    cached_data = st.session_state.cached_answer_data
    
    # User question
    st.markdown(f"""
    <div style="margin-bottom: 1rem;">
        <div style="display: flex; justify-content: flex-end; margin-bottom: 0.3rem;">
            <div class="user-bubble">
                <div style="font-weight: 600; margin-bottom: 0.2rem;">👤 আপুনি:</div>
                <div>{question[:200]}{'...' if len(question) > 200 else ''}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Cached answer with indicator
    cache_source = "Memory" if st.session_state.current_cache_key in st.session_state.cache_manager.memory_cache else "Supabase"
    
    st.markdown(f"""
    <div style="margin-bottom: 0.5rem;">
        <div style="display: flex; align-items: flex-start; margin-bottom: 0.3rem;">
            <div style="margin-right: 0.5rem; font-size: 1.2rem;">🤖</div>
            <div style="flex: 1;">
                <div class="ai-bubble">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; padding-bottom: 0.5rem; border-bottom: 2px solid #4CAF50;">
                        <div style="display: flex; align-items: center;">
                            <div style="background: #4CAF50; color: white; padding: 0.2rem 0.5rem; border-radius: 8px; 
                                        font-weight: 600; font-size: 0.8rem; margin-right: 0.5rem;">
                                <span style="margin-right: 0.3rem;">⚡</span> Cached Answer
                            </div>
                            <div style="font-weight: 600; color: #0d47a1; font-size: 0.9rem;">
                                {cached_data.get('subject', selected_subject)} • {cached_data.get('chapter', current_chapter_name)}
                            </div>
                        </div>
                        <div style="font-size: 0.75rem; color: #666; background: #f1f8e9; padding: 0.2rem 0.5rem; border-radius: 4px;">
                            <span style="margin-right: 0.3rem;">💾</span> From {cache_source}
                        </div>
                    </div>
                    <div style="color: #333; line-height: 1.5; font-size: 0.95rem;">
                        {cached_data['answer']}
                    </div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Show token usage
    if cached_data.get('tokens', 0) > 0:
        st.caption(f"📊 Original token cost (saved): {cached_data['tokens']:,} tokens")
    
    # Reset flag
    st.session_state.show_cached_answer = False
    if 'cached_answer_data' in st.session_state:
        del st.session_state.cached_answer_data
    if 'current_cache_key' in st.session_state:
        del st.session_state.current_cache_key

# ===============================
# PROCESS QUESTION WITH STREAMING
# ===============================
if st.session_state.get('processing') and question and api_key:
    st.markdown("---")
    
    # User question
    st.markdown(f"""
    <div style="margin-bottom: 1rem;">
        <div style="display: flex; justify-content: flex-end; margin-bottom: 0.3rem;">
            <div class="user-bubble">
                <div style="font-weight: 600; margin-bottom: 0.2rem;">👤 আপুনি:</div>
                <div>{question[:200]}{'...' if len(question) > 200 else ''}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # AI answer header (with thinking animation)
    st.markdown(f"""
    <div style="margin-bottom: 0.5rem;">
        <div style="display: flex; align-items: flex-start; margin-bottom: 0.3rem;">
            <div style="margin-right: 0.5rem; font-size: 1.2rem;">🤖</div>
            <div style="flex: 1;">
                <div class="ai-bubble">
                    <div style="display: flex; align-items: center; margin-bottom: 0.5rem; padding-bottom: 0.5rem; border-bottom: 2px solid #2196F3;">
                        <div style="background: #2196F3; color: white; padding: 0.2rem 0.5rem; border-radius: 8px; 
                                    font-weight: 600; font-size: 0.8rem; margin-right: 0.5rem;">
                            AI টিউটাৰ
                        </div>
                        <div style="font-weight: 600; color: #0d47a1; font-size: 0.9rem;">
                            {selected_subject} • {current_chapter_name}
                        </div>
                    </div>
                    <div style="color: #333; line-height: 1.5; font-size: 0.95rem; min-height: 100px;">
    """, unsafe_allow_html=True)
    
    # Show thinking animation while preparing response
    thinking_placeholder = st.empty()
    thinking_placeholder.markdown("""
    <div class="progress-indicator">
        <span>উত্তৰ প্ৰস্তুত কৰি আছো...</span>
        <div class="thinking-dots">
            <span></span>
            <span></span>
            <span></span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Get the prompt and stream the response
    system_prompt = get_subject_prompt(selected_subject, current_chapter_name, question)
    
    # Clear thinking animation and start streaming
    thinking_placeholder.empty()
    
    # Stream the response
    stream_deepseek_response(system_prompt, question, selected_subject, current_chapter_name)
    
    # Close the AI bubble div
    st.markdown("""
                    </div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Show token usage
    if st.session_state.tokens_used > 0:
        estimated_cost = st.session_state.tokens_used * 0.0000014
        st.caption(f"📊 ট'কেন ব্যৱহৃত: {st.session_state.tokens_used:,} (Cost: ${estimated_cost:.6f})")
    
    st.session_state.processing = False

# ===============================
# HISTORY
# ===============================
if st.session_state.history:
    st.markdown("---")
    st.markdown("#### 📜 আজিৰ প্ৰশ্নাৱলী")
    
    for i, item in enumerate(reversed(st.session_state.history[-5:]), 1):
        cache_indicator = " ⚡" if item.get('cached') else " 🤖"
        cache_source = f" ({item.get('cache_source', 'API')})" if item.get('cached') else ""
        
        with st.expander(f"প্ৰশ্ন {i}: {item['question']} ({item['timestamp']}{cache_indicator}{cache_source})"):
            st.write(f"**বিষয়:** {item['subject']}")
            st.write(f"**অধ্যায়:** {item['chapter']}")
            st.write(f"**ট'কেন:** {item.get('tokens', 0):,}")
            if item.get('cached'):
                st.caption(f"⚡ This answer was served from {item.get('cache_source', 'cache')}")

# ===============================
# FOOTER
# ===============================
st.markdown("---")
st.markdown("""
<div style="text-align: center; padding: 0.5rem;">
    <h3 style="color: #0d47a1; margin-bottom: 0.5rem;">
        🎓 আপোনাৰ সফলতাৰ বাবে মই সদায় আছো!
    </h3>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div style="text-align: center; padding: 0.5rem; margin-top: 1rem; color: #1976D2; font-size: 0.8rem;">
    <p style="margin: 0;">© 2025 Jajabor AI. All rights reserved.</p>
</div>
""", unsafe_allow_html=True)
