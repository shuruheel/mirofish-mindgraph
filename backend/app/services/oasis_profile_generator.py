"""
OASIS Agent Profile Generator
Converts entities from the knowledge graph into Agent Profile format required by the OASIS simulation platform.

Optimizations:
1. Call graph retrieval to enrich node information
2. Optimized prompts to generate highly detailed personas
3. Distinguish between individual entities and abstract group entities
"""

import json
import os
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from ..utils.mindgraph_client import MindGraphClient
from .entity_reader import EntityNode, EntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """OASIS Agent Profile data structure"""
    # Common fields
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str
    
    # Optional fields - Reddit style
    karma: int = 1000
    
    # Optional fields - Twitter style
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # Additional persona information
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # Source entity information
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Convert to Reddit platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS library requires field name username (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # Add additional persona information (if available)
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Convert to Twitter platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS library requires field name username (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # Add additional persona information
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to complete dictionary format"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS Profile Generator
    
    Converts entities from the knowledge graph into Agent Profiles required for OASIS simulation
    
    Optimized features:
    1. Call graph retrieval for richer context
    2. Generate highly detailed personas (including basic info, career history, personality traits, social media behavior, etc.)
    3. Distinguish between individual entities and abstract group entities
    """
    
    # MBTI type list
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # Common country list
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France", 
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]
    
    # Individual entity types (require specific persona generation)
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # Group/institutional entity types (require representative persona generation)
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo", 
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        graph_id: Optional[str] = None,
        source: str = "upload"
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        self.source = source

        if not self.api_key:
            raise ValueError("LLM_API_KEY not configured")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        # MindGraph client for enriched context retrieval
        self.graph_client = None
        self.graph_id = graph_id

        if Config.MINDGRAPH_API_KEY:
            try:
                self.graph_client = MindGraphClient()
            except Exception as e:
                logger.warning(f"MindGraph client initialization failed: {e}")
    
    def generate_profile_from_entity(
        self, 
        entity: EntityNode, 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        Generate OASIS Agent Profile from graph entity
        
        Args:
            entity: Graph entity node
            user_id: User ID (for OASIS)
            use_llm: Whether to use LLM for detailed persona generation
            
        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"
        
        # Basic information
        name = entity.name
        user_name = self._generate_username(name)
        
        # Build context information
        context = self._build_entity_context(entity)
        
        if use_llm:
            # Use LLM to generate detailed persona
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # Use rules to generate basic persona
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _generate_username(self, name: str) -> str:
        """Generate username"""
        # Remove special characters, convert to lowercase
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # Add random suffix to avoid duplicates
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    # Entities with this many related edges already have enough structural
    # context from the graph — skip the RAG call to save latency and cost.
    RAG_SKIP_EDGE_THRESHOLD = 3

    def _search_graph_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        Use MindGraph semantic search to get supplementary entity information

        Only called when entity structural context is insufficient (related edges < RAG_SKIP_EDGE_THRESHOLD).
        Skips raw text chunks (include_chunks=False) to reduce tokens and latency.

        Args:
            entity: Entity node object

        Returns:
            Dictionary containing node_summaries and context
        """
        empty = {"node_summaries": [], "context": ""}

        if not self.graph_client or not self.graph_id:
            return empty

        # Skip RAG for entities with sufficient structural context
        edge_count = len(entity.related_edges) if entity.related_edges else 0
        if edge_count >= self.RAG_SKIP_EDGE_THRESHOLD:
            logger.debug(f"Skipping RAG retrieval: {entity.name} already has {edge_count} edges")
            return empty

        entity_name = entity.name
        results = dict(empty)

        try:
            rag_project_id = None if self.source == "mindgraph" else self.graph_id
            ctx_result = self.graph_client.retrieve_context(
                query=entity_name,
                project_id=rag_project_id,
                k=3,
                depth=1,
                include_chunks=False,
            )

            all_summaries = set()

            # Extract summaries from graph nodes
            graph_data = ctx_result.get("graph", {})
            for node in graph_data.get("nodes", []):
                summary = node.get("summary", "")
                if summary:
                    all_summaries.add(summary)
                name = node.get("label", node.get("name", ""))
                if name and name != entity_name:
                    all_summaries.add(f"Related entity: {name}")

            # Fallback format
            for item in ctx_result.get("results", []):
                content = item.get("summary", "") or item.get("label", "")
                if content:
                    all_summaries.add(content)

            results["node_summaries"] = list(all_summaries)

            if results["node_summaries"]:
                results["context"] = "Related entities:\n" + "\n".join(
                    f"- {s}" for s in results["node_summaries"][:10]
                )

            logger.debug(f"RAG retrieval: {entity_name}, {len(results['node_summaries'])}  related nodes")

        except Exception as e:
            logger.warning(f"Graph retrieval failed ({entity_name}): {e}")

        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        Build complete context information for entity
        
        Includes:
        1. Entity edge information (facts)
        2. Related node details
        3. Rich information from graph hybrid retrieval
        """
        context_parts = []
        
        # 1. Add entity attribute information
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Entity attributes\n" + "\n".join(attrs))
        
        # 2. Add related edge information (facts/relationships)
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # No limit
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (related entity)")
                    else:
                        relationships.append(f"- (related entity) --[{edge_name}]--> {entity.name}")
            
            if relationships:
                context_parts.append("### Related Facts and Relationships\n" + "\n".join(relationships))
        
        # 3. Add related node details (including attributes)
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # No limit
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                node_attrs = node.get("attributes", {})

                # Filter out default labels
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""

                parts = [f"- **{node_name}**{label_str}"]
                if node_summary:
                    parts.append(f": {node_summary}")

                # Include key attributes (description, entity_type, etc.)
                desc = node_attrs.get("description", "")
                if desc and desc != node_summary:
                    parts.append(f"\n  Description: {desc}")
                node_et = node_attrs.get("entity_type", "")
                if node_et:
                    parts.append(f"\n  Type: {node_et}")

                related_info.append("".join(parts))

            if related_info:
                context_parts.append("### Related Entity Information\n" + "\n".join(related_info))
        
        # 4. Supplementary RAG retrieval (only when structural context is insufficient)
        graph_results = self._search_graph_for_entity(entity)

        if graph_results.get("node_summaries"):
            context_parts.append("### Related Nodes from Graph Retrieval\n" + "\n".join(f"- {s}" for s in graph_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """Determine if entity is an individual type"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """Determine if entity is a group/institutional type"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        Use LLM to generate highly detailed persona
        
        Distinguished by entity type:
        - Individual entities: Generate specific character settings
        - Group/institutional entities: Generate representative account settings
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # Try multiple times until success or max retries reached
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # Lower temperature on each retry
                    # No max_tokens set, let LLM generate freely
                )
                
                content = response.choices[0].message.content
                
                # Check if output was truncated (finish_reason is not stop)
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"LLM output truncated (attempt {attempt+1}), attempting fix...")
                    content = self._fix_truncated_json(content)
                
                # Try to parse JSON
                try:
                    result = json.loads(content)
                    
                    # Validate required fields
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name} is a {entity_type}."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"JSON parse failed (attempt {attempt+1}): {str(je)[:80]}")
                    
                    # Try to fix JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # Exponential backoff
        
        logger.warning(f"LLM persona generation failed ({max_attempts} attempts): {last_error}, using rule-based generation")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Fix truncated JSON (output truncated by max_tokens limit)"""
        import re
        
        # If JSON was truncated, try to close it
        content = content.strip()
        
        # Count unclosed brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check for unclosed strings
        # Simple check: if no comma or closing bracket after last quote, string may be truncated
        if content and content[-1] not in '",}]':
            # Try to close string
            content += '"'
        
        # Close brackets
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Try to fix corrupted JSON"""
        import re
        
        # 1. First try to fix truncation
        content = self._fix_truncated_json(content)
        
        # 2. Try to extract JSON portion
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. Handle newline issues in strings
            # Find all string values and replace newlines within them
            def fix_string_newlines(match):
                s = match.group(0)
                # Replace actual newlines in strings with spaces
                s = s.replace('\n', ' ').replace('\r', ' ')
                # Replace excess whitespace
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # Match JSON string values
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. Try to parse
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. If still failing, try more aggressive fix
                try:
                    # Remove all control characters
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # Replace all consecutive whitespace
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. Try to extract partial information from content
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # May be truncated
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} is a {entity_type}.")
        
        # If meaningful content was extracted, mark as fixed
        if bio_match or persona_match:
            logger.info(f"Extracted partial information from corrupted JSON")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. Complete failure, return basic structure
        logger.warning(f"JSON fix failed, returning basic structure")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} is a {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Get system prompt"""
        base_prompt = "You are a social media user persona generation expert. Generate detailed, realistic personas for public opinion simulation, maximizing fidelity to existing real-world conditions. Must return valid JSON format; all string values must not contain unescaped newlines. Write in English."
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Build detailed persona prompt for individual entities"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "None"
        context_str = context[:3000] if context else "No additional context"
        
        return f"""Generate a detailed social media user persona for this entity, maximizing fidelity to existing real-world conditions.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context information:
{context_str}

Please generate JSON with the following fields:

1. bio: Social media bio, 200 characters
2. persona: Detailed persona description (2000-character plain text), must include:
   - Basic info (age, profession, education, location)
   - Background (important experiences, connection to events, social relationships)
   - Personality traits (MBTI type, core personality, emotional expression style)
   - Social media behavior (posting frequency, content preferences, interaction style, language features)
   - Stance and viewpoints (attitudes on topics, content that may provoke/move them)
   - Unique characteristics (catchphrases, special experiences, personal hobbies)
   - Personal memories (important part of the persona: introduce this individual's connection to the event, and their existing actions and reactions in the event)
3. age: Age number (must be integer)
4. gender: Gender, must be in English: "male" or "female"
5. mbti: MBTI type (e.g., INTJ, ENFP)
6. country: Country name
7. profession: Profession
8. interested_topics: Array of topics of interest

Important:
- All field values must be strings or numbers, do not use newlines
- persona must be a coherent text description
- Write in English
- Content must be consistent with entity information
- age must be a valid integer, gender must be "male" or "female"
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Build detailed persona prompt for group/institutional entities"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "None"
        context_str = context[:3000] if context else "No additional context"
        
        return f"""Generate a detailed social media account profile for this institutional/group entity, maximizing fidelity to existing real-world conditions.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context information:
{context_str}

Please generate JSON with the following fields:

1. bio: Official account bio, 200 characters, professional and appropriate
2. persona: Detailed account profile description (2000-character plain text), must include:
   - Institution basic info (official name, institution type, founding background, main functions)
   - Account positioning (account type, target audience, core functions)
   - Communication style (language features, common expressions, taboo topics)
   - Content characteristics (content types, posting frequency, active time periods)
   - Stance (official position on core topics, approach to handling controversy)
   - Special notes (represented group profile, operational habits)
   - Institutional memory (important part of the persona: introduce this institution's connection to the event, and their existing actions and reactions in the event)
3. age: Fixed at 30 (virtual age for institutional accounts)
4. gender: Fixed as "other" (institutional accounts use other for non-individual)
5. mbti: MBTI type describing account style, e.g., ISTJ for rigorous and conservative
6. country: Country name
7. profession: Institution function description
8. interested_topics: Array of areas of interest

Important:
- All field values must be strings or numbers, null values not allowed
- persona must be a coherent text description, do not use newlines
- Write in English (gender field must be "other")
- age must be integer 30, gender must be string "other"
- Institutional account communication must match its identity positioning"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate basic persona using rules"""
        
        # Generate different personas based on entity type
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # Institutional virtual age
                "gender": "other",  # Institutions use other
                "mbti": "ISTJ",  # Institutional style: rigorous and conservative
                "country": "China",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # Institutional virtual age
                "gender": "other",  # Institutions use other
                "mbti": "ISTJ",  # Institutional style: rigorous and conservative
                "country": "China",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # Default persona
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """Set graph ID for graph retrieval"""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        Batch generate Agent Profiles from entities (supports parallel generation)
        
        Args:
            entities: Entity list
            use_llm: Whether to use LLM for detailed persona generation
            progress_callback: Progress callback function (current, total, message)
            graph_id: Graph ID for graph retrieval to get richer context
            parallel_count: Parallel generation count, default 5
            realtime_output_path: Real-time output file path (if provided, writes after each generation)
            output_platform: Output platform format ("reddit" or "twitter")
            
        Returns:
            List of Agent Profiles
        """
        import concurrent.futures
        from threading import Lock
        
        # Set graph_id for MindGraph retrieval
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total  # Pre-allocate list to maintain order
        completed_count = [0]  # Use list to allow modification in closure
        lock = Lock()
        
        # Helper function for real-time file writing
        def save_profiles_realtime():
            """Save generated profiles to file in real-time"""
            if not realtime_output_path:
                return
            
            with lock:
                # Filter out generated profiles
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return

                try:
                    # Atomic write: write to temp file then rename to avoid
                    # corrupted JSON when readers poll during writes
                    tmp_path = realtime_output_path + ".tmp"
                    if output_platform == "reddit":
                        # Reddit JSON format
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(tmp_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV format
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                    os.replace(tmp_path, realtime_output_path)
                except Exception as e:
                    logger.warning(f"Real-time profile save failed: {e}")
                    # Clean up temp file on failure
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        
        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """Worker function for generating a single profile"""
            entity_type = entity.get_entity_type() or "Entity"
            
            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )
                
                # Output generated persona to console and logs in real-time
                self._print_generated_profile(entity.name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"Failed to generate persona for entity {entity.name}: {str(e)}")
                # Create a basic profile
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        # ===== Resume: load existing partial profiles from disk =====
        resumed_count = 0
        if realtime_output_path and os.path.exists(realtime_output_path):
            try:
                with open(realtime_output_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                if isinstance(existing_data, list) and existing_data:
                    # Build a set of already-generated user_ids
                    existing_ids = {p.get("user_id") for p in existing_data}
                    for item in existing_data:
                        uid = item.get("user_id")
                        if uid is not None and 0 <= uid < total:
                            profiles[uid] = OasisAgentProfile(
                                user_id=uid,
                                user_name=item.get("username", f"agent_{uid}"),
                                name=item.get("name", ""),
                                bio=item.get("bio", ""),
                                persona=item.get("persona", ""),
                                karma=item.get("karma", 1000),
                                age=item.get("age"),
                                gender=item.get("gender"),
                                mbti=item.get("mbti"),
                                country=item.get("country"),
                                profession=item.get("profession"),
                                interested_topics=item.get("interested_topics", []),
                            )
                            resumed_count += 1
                    completed_count[0] = resumed_count
                    logger.info(f"Resumed {resumed_count}/{total} existing personas from disk")
            except Exception as e:
                logger.warning(f"Failed to load existing personas, regenerating all: {e}")
                resumed_count = 0

        # Only generate profiles for entities that don't have one yet
        entities_to_generate = [
            (idx, entity) for idx, entity in enumerate(entities)
            if profiles[idx] is None
        ]
        remaining = len(entities_to_generate)

        logger.info(f"Starting parallel generation of {remaining} Agent personas (skipping {resumed_count} existing, parallelism: {parallel_count})...")
        print(f"\n{'='*60}")
        print(f"Generating Agent personas - {total} total entities, {resumed_count} existing, {remaining} to generate, parallelism: {parallel_count}")
        print(f"{'='*60}\n")

        if progress_callback and resumed_count > 0:
            progress_callback(
                resumed_count, total,
                f"Resumed {resumed_count}/{total} existing personas from disk"
            )

        # Use thread pool for parallel execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # Submit only tasks that need generation
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in entities_to_generate
            }
            
            # Collect results
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # Write to file in real-time
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"Completed {current}/{total}: {entity.name} ({entity_type})"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} using fallback persona: {error}")
                    else:
                        logger.info(f"[{current}/{total}] Successfully generated persona: {entity.name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"Exception processing entity {entity.name}: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # Write to file in real-time (even for fallback personas)
                    save_profiles_realtime()
        
        print(f"\n{'='*60}")
        print(f"Persona generation complete! Generated {len([p for p in profiles if p])} Agents")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Output generated persona to console in real-time (full content, not truncated)"""
        separator = "-" * 70
        
        # Build complete output content (not truncated)
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'None'
        
        output_lines = [
            f"\n{separator}",
            f"[Generated] {entity_name} ({entity_type})",
            f"{separator}",
            f"Username: {profile.user_name}",
            f"",
            f"[Bio]",
            f"{profile.bio}",
            f"",
            f"[Detailed Persona]",
            f"{profile.persona}",
            f"",
            f"[Basic Attributes]",
            f"Age: {profile.age} | Gender: {profile.gender} | MBTI: {profile.mbti}",
            f"Profession: {profile.profession} | Country: {profile.country}",
            f"Interests: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # Output to console only (avoid duplication, logger no longer outputs full content)
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        Save Profiles to file (select correct format by platform)
        
        OASIS platform format requirements:
        - Twitter: CSV format
        - Reddit: JSON format
        
        Args:
            profiles: Profile list
            file_path: File path
            platform: Platform type ("reddit" or "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Save Twitter Profiles as CSV format (per OASIS official requirements)
        
        OASIS Twitter required CSV fields:
        - user_id: User ID (sequential from 0 based on CSV order)
        - name: User real name
        - username: System username
        - user_char: Detailed persona description (injected into LLM system prompt, guides Agent behavior)
        - description: Short public bio (displayed on user profile page)
        
        user_char vs description distinction:
        - user_char: Internal use, LLM system prompt, determines how Agent thinks and acts
        - description: External display, bio visible to other users
        """
        import csv
        
        # Ensure file extension is .csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write OASIS required header
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # Write data rows
            for idx, profile in enumerate(profiles):
                # user_char: Complete persona (bio + persona), for LLM system prompt
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # Handle newlines (replace with spaces in CSV)
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # description: Short bio for external display
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,                    # user_id: Sequential ID starting from 0
                    profile.name,           # name: Real name
                    profile.user_name,      # username: Username
                    user_char,              # user_char: Complete persona (internal LLM use)
                    description             # description: Short bio (external display)
                ]
                writer.writerow(row)
        
        logger.info(f"Saved {len(profiles)} Twitter Profiles to {file_path} (OASIS CSV format)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        Standardize gender field to OASIS required English format
        
        OASIS requires: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        gender_map = {
            "male": "male",
            "female": "female",
            "other": "other",
            "institution": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Save Reddit Profiles as JSON format
        
        Uses format consistent with to_reddit_format(), ensuring OASIS can read correctly.
        Must include user_id field -- this is critical for OASIS agent_graph.get_agent() matching!
        
        Required fields:
        - user_id: User ID (integer, for matching poster_agent_id in initial_posts)
        - username: Username
        - name: Display name
        - bio: Bio
        - persona: Detailed persona
        - age: Age (integer)
        - gender: "male", "female", or "other"
        - mbti: MBTI type
        - country: Country
        """
        data = []
        for idx, profile in enumerate(profiles):
            # Use format consistent with to_reddit_format()
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # Critical: must include user_id
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # OASIS required fields - ensure all have defaults
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "China",
            }
            
            # Optional fields
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(profiles)} Reddit Profiles to {file_path} (JSON format, includes user_id field)")
    
    # Keep old method name as alias for backward compatibility
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[Deprecated] Please use save_profiles() method"""
        logger.warning("save_profiles_to_json is deprecated, please use save_profiles method")
        self.save_profiles(profiles, file_path, platform)

