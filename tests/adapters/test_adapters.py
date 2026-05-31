"""Adapter tests for offline implementations."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

from src.adapters import (
    ArxivSourceAdapter,
    CompositeSourceAdapter,
    GitHubReleasesSourceAdapter,
    GoogleNewsRSSSourceAdapter,
    HackerNewsSourceAdapter,
    LLMResult,
    LocalJsonSourceAdapter,
    MockLLMAdapter,
    ModelPricing,
    OpenAICompatibleLLMAdapter,
    RSSSourceAdapter,
    SourceAdapter,
    create_source_adapter,
    create_llm_adapter,
    estimate_llm_cost_usd,
    find_model_pricing,
    pricing_table_from_config,
)
from src.schemas import RawNewsItem


class AdapterTests(unittest.TestCase):
    def test_local_json_source_collects_json_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "raw.json"
            payload = [{"id": "raw-1", "title": "Example"}]
            path.write_text(json.dumps(payload), encoding="utf-8")

            items = LocalJsonSourceAdapter(path).collect()

        self.assertEqual(items, payload)

    def test_local_json_source_requires_array_of_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "raw.json"
            path.write_text(json.dumps({"id": "raw-1"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must contain a JSON array"):
                LocalJsonSourceAdapter(path).collect()

    def test_rss_source_maps_feed_entry_to_raw_item(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI Startup raises funding",
                        "https://example.com/ai-funding",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                )
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "AI Startup raises funding")
        self.assertEqual(items[0]["source"], "TechCrunch")
        self.assertEqual(items[0]["metadata"]["source_name"], "techcrunch_ai")
        self.assertEqual(items[0]["metadata"]["fetched_via"], "rss")
        self.assertEqual(items[0]["metadata"]["content_source"], "rss_feed")

    def test_rss_source_candidate_keywords_keep_only_ai_items_from_title_or_summary(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed_two_items(
                        first_title="IT之家：AI PC 市场继续升温",
                        first_link="https://example.com/ithome-ai",
                        first_description="厂商正在加快人工智能终端布局。",
                        second_title="IT之家：新款显示器发布",
                        second_link="https://example.com/ithome-monitor",
                        second_description="这是一条普通消费电子新闻。",
                    ),
                )
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "ithome_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://www.ithome.com/rss/",
                "source": "IT之家",
                "source_type": "news",
                "language": "zh",
                "candidate_keywords": ["AI", "人工智能", "大模型", "智能体"],
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "IT之家：AI PC 市场继续升温")
        self.assertEqual(items[0]["source"], "IT之家")

    def test_rss_source_candidate_keywords_match_summary_when_title_is_generic(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "IT之家：今日观察",
                        "https://example.com/ithome-summary-match",
                        description="文章讨论大模型和智能体在企业中的落地进展。",
                    ),
                )
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "ithome_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://www.ithome.com/rss/",
                "source": "IT之家",
                "source_type": "news",
                "language": "zh",
                "candidate_keywords": ["AI", "人工智能", "大模型", "智能体"],
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/ithome-summary-match")

    def test_rss_source_can_fetch_article_html_for_full_content(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI Startup raises funding",
                        "https://example.com/ai-funding",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                ),
                FakeResponse(200, _article_html(), headers={"Content-Type": "text/html"}),
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
                "fetch_full_content": True,
                "full_content_min_chars": 80,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertIn("First full article paragraph", items[0]["content"])
        self.assertIn("Second full article paragraph", items[0]["content"])
        self.assertNotIn("Subscribe to our newsletter", items[0]["content"])
        self.assertEqual(items[0]["metadata"]["content_source"], "article_html")
        self.assertTrue(items[0]["metadata"]["full_content_enabled"])
        self.assertGreater(items[0]["metadata"]["full_content_chars"], 80)
        self.assertFalse(items[0]["metadata"]["content_truncated"])

    def test_rss_source_does_not_truncate_full_content_by_default(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI Startup raises funding",
                        "https://example.com/ai-funding",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                ),
                FakeResponse(200, _long_article_html(), headers={"Content-Type": "text/html"}),
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
                "fetch_full_content": True,
                "full_content_min_chars": 80,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertIn("Final paragraph marker", items[0]["content"])
        self.assertFalse(items[0]["metadata"]["content_truncated"])

    def test_rss_source_can_extract_chinese_full_content(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "量子位：大模型智能体进入企业落地阶段",
                        "https://example.com/qbitai-agent",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                ),
                FakeResponse(200, _zh_article_html(), headers={"Content-Type": "text/html; charset=UTF-8"}),
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "qbitai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://www.qbitai.com/feed",
                "source": "量子位",
                "source_type": "news",
                "language": "zh",
                "fetch_full_content": True,
                "full_content_min_chars": 120,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertIn("企业开始把智能体系统真正接入客服、销售和研发流程", items[0]["content"])
        self.assertIn("推理成本和模型稳定性", items[0]["content"])
        self.assertEqual(items[0]["metadata"]["content_source"], "article_html")
        self.assertGreaterEqual(items[0]["metadata"]["content_chars"], 120)

    def test_rss_source_only_truncates_full_content_when_limit_is_configured(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI Startup raises funding",
                        "https://example.com/ai-funding",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                ),
                FakeResponse(200, _long_article_html(), headers={"Content-Type": "text/html"}),
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
                "fetch_full_content": True,
                "full_content_min_chars": 80,
                "full_content_max_chars": 220,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertLessEqual(len(items[0]["content"]), 220)
        self.assertNotIn("Final paragraph marker", items[0]["content"])
        self.assertTrue(items[0]["metadata"]["content_truncated"])

    def test_rss_source_keeps_feed_content_when_article_fetch_fails(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI Startup raises funding",
                        "https://example.com/ai-funding",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                ),
                requests.Timeout("request timed out"),
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
                "fetch_full_content": True,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(items[0]["content"], "AI summary with markup.")
        self.assertEqual(items[0]["metadata"]["content_source"], "rss_feed")
        self.assertIn("Timeout", items[0]["metadata"]["full_content_error"])

    def test_rss_source_retries_timeout_before_success(self) -> None:
        session = FakeSession(
            [
                requests.Timeout("request timed out"),
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI Startup raises funding",
                        "https://example.com/ai-funding",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                ),
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
                "retry_attempts": 2,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(adapter.request_attempts, 2)
        self.assertEqual(adapter.request_errors[0]["category"], "network_error")
        self.assertTrue(adapter.request_errors[0]["retryable"])

    def test_rss_source_records_rate_limit_without_retry_when_attempts_exhausted(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    429,
                    "rate limited",
                    reason="Too Many Requests",
                    headers={"Retry-After": "30"},
                )
            ]
        )
        adapter = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "language": "en",
                "retry_attempts": 1,
            },
            session=session,
        )

        with self.assertRaises(requests.HTTPError):
            adapter.collect()

        self.assertEqual(adapter.request_errors[0]["status_code"], 429)
        self.assertEqual(adapter.request_errors[0]["category"], "rate_limited")
        self.assertEqual(adapter.request_errors[0]["retry_after"], "30")

    def test_google_news_rss_builds_query_url(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _rss_feed(
                        "AI market news",
                        "https://example.com/ai-market",
                        "Wed, 20 May 2026 10:00:00 GMT",
                    ),
                )
            ]
        )
        adapter = GoogleNewsRSSSourceAdapter(
            {
                "name": "google_news_ai",
                "family": "aggregator",
                "type": "google_news_rss",
                "query": "AI OR LLM",
                "source": "Google News",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertIn("news.google.com/rss/search", session.calls[0]["args"][0])
        self.assertEqual(items[0]["metadata"]["fetched_via"], "rss")

    def test_arxiv_source_maps_atom_entry(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    _atom_feed(
                        "A benchmark for AI agents",
                        "https://arxiv.org/abs/2605.00001",
                        "2026-05-20T10:00:00Z",
                    ),
                )
            ]
        )
        adapter = ArxivSourceAdapter(
            {
                "name": "arxiv_ai",
                "family": "official_channel",
                "type": "arxiv_api",
                "query": "cat:cs.AI",
                "source": "arXiv",
                "source_type": "research",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_type"], "research")
        self.assertEqual(items[0]["metadata"]["fetched_via"], "arxiv_api")
        self.assertEqual(items[0]["metadata"]["query"], "cat:cs.AI")
        self.assertEqual(items[0]["metadata"]["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(items[0]["metadata"]["categories"], ["cs.AI", "cs.LG"])
        self.assertEqual(items[0]["metadata"]["primary_category"], "cs.AI")
        self.assertEqual(items[0]["metadata"]["pdf_url"], "https://arxiv.org/pdf/2605.00001")

    def test_github_releases_source_maps_release_json(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "id": 123,
                            "name": "v1.0.0",
                            "tag_name": "v1.0.0",
                            "html_url": "https://github.com/example/repo/releases/tag/v1.0.0",
                            "published_at": "2026-05-20T10:00:00Z",
                            "body": "Adds faster inference for AI workloads.",
                        }
                    ],
                )
            ]
        )
        adapter = GitHubReleasesSourceAdapter(
            {
                "name": "github_releases_ai_tools",
                "family": "official_channel",
                "type": "github_releases",
                "repos": ["example/repo"],
                "source": "GitHub Releases",
                "source_type": "release",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "example/repo v1.0.0")
        self.assertEqual(items[0]["metadata"]["repo"], "example/repo")
        self.assertEqual(items[0]["metadata"]["fetched_via"], "github_releases")

    def test_github_releases_source_uses_token_and_round_robins_repos(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    [
                        _github_release(1, "repo-a", "v1.0.0"),
                        _github_release(2, "repo-a", "v0.9.0"),
                    ],
                ),
                FakeResponse(
                    200,
                    [
                        _github_release(3, "repo-b", "v2.0.0"),
                        _github_release(4, "repo-b", "v1.9.0"),
                    ],
                ),
            ]
        )
        adapter = GitHubReleasesSourceAdapter(
            {
                "name": "github_releases_ai_tools",
                "family": "official_channel",
                "type": "github_releases",
                "repos": ["example/repo-a", "example/repo-b"],
                "source": "GitHub Releases",
                "source_type": "release",
                "max_items": 3,
                "token": "test-token",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(
            [item["metadata"]["repo"] for item in items],
            ["example/repo-a", "example/repo-b", "example/repo-a"],
        )
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(session.calls[0]["headers"]["Accept"], "application/vnd.github+json")

    def test_github_releases_source_skips_rate_limited_repo(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    403,
                    {"message": "API rate limit exceeded"},
                    reason="Forbidden",
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1779280800",
                    },
                ),
                FakeResponse(200, [_github_release(3, "repo-b", "v2.0.0")]),
            ]
        )
        adapter = GitHubReleasesSourceAdapter(
            {
                "name": "github_releases_ai_tools",
                "family": "official_channel",
                "type": "github_releases",
                "repos": ["example/repo-a", "example/repo-b"],
                "source": "GitHub Releases",
                "source_type": "release",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["metadata"]["repo"], "example/repo-b")
        self.assertEqual(adapter.rate_limit_errors[0]["repo"], "example/repo-a")
        self.assertEqual(adapter.rate_limit_errors[0]["rate_limit_remaining"], "0")

    def test_hackernews_source_maps_story_json(self) -> None:
        session = FakeSession(
            [
                FakeResponse(200, [42]),
                FakeResponse(
                    200,
                    {
                        "id": 42,
                        "type": "story",
                        "title": "OpenAI releases an AI agent",
                        "url": "https://example.com/agent",
                        "time": 1779280800,
                        "score": 50,
                        "descendants": 12,
                    },
                ),
            ]
        )
        adapter = HackerNewsSourceAdapter(
            {
                "name": "hackernews_ai",
                "family": "social_discussion",
                "type": "hackernews_api",
                "lists": ["topstories"],
                "keywords": ["ai", "openai"],
                "source": "Hacker News",
                "source_type": "forum",
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "Hacker News")
        self.assertEqual(items[0]["metadata"]["hn_id"], "42")
        self.assertEqual(items[0]["metadata"]["fetched_via"], "hackernews_api")
        self.assertEqual(items[0]["metadata"]["hn_story_kind"], "external_link")
        self.assertTrue(items[0]["metadata"]["is_external_link"])

    def test_hackernews_source_classifies_internal_discussion_types(self) -> None:
        session = FakeSession(
            [
                FakeResponse(200, [1, 2, 3]),
                FakeResponse(
                    200,
                    {
                        "id": 1,
                        "type": "story",
                        "title": "Ask HN: How do you evaluate AI agents?",
                        "text": "What benchmarks do you trust?",
                        "time": 1779280800,
                        "score": 10,
                        "descendants": 5,
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "id": 2,
                        "type": "story",
                        "title": "Show HN: AI debugger for Python",
                        "url": "https://example.com/debugger",
                        "time": 1779280800,
                        "score": 20,
                        "descendants": 8,
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "id": 3,
                        "type": "story",
                        "title": "OpenAI board discussion",
                        "time": 1779280800,
                        "score": 30,
                        "descendants": 13,
                    },
                ),
            ]
        )
        adapter = HackerNewsSourceAdapter(
            {
                "name": "hackernews_ai",
                "family": "social_discussion",
                "type": "hackernews_api",
                "lists": ["topstories"],
                "keywords": ["ai", "openai"],
                "source": "Hacker News",
                "source_type": "forum",
                "max_items": 3,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(
            [item["metadata"]["hn_story_kind"] for item in items],
            ["ask_hn", "show_hn", "internal_discussion"],
        )
        self.assertFalse(items[0]["metadata"]["is_external_link"])
        self.assertIn("What benchmarks do you trust?", items[0]["summary"])

    def test_hackernews_source_can_exclude_internal_discussions(self) -> None:
        session = FakeSession(
            [
                FakeResponse(200, [1, 2]),
                FakeResponse(
                    200,
                    {
                        "id": 1,
                        "type": "story",
                        "title": "Ask HN: AI agent benchmarks",
                        "text": "Which one works?",
                        "time": 1779280800,
                        "score": 10,
                        "descendants": 5,
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "id": 2,
                        "type": "story",
                        "title": "AI release notes",
                        "url": "https://example.com/release",
                        "time": 1779280800,
                        "score": 20,
                        "descendants": 8,
                    },
                ),
            ]
        )
        adapter = HackerNewsSourceAdapter(
            {
                "name": "hackernews_ai",
                "family": "social_discussion",
                "type": "hackernews_api",
                "lists": ["topstories"],
                "keywords": ["ai"],
                "source": "Hacker News",
                "source_type": "forum",
                "include_discussions": False,
            },
            session=session,
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["metadata"]["hn_story_kind"], "external_link")

    def test_create_source_adapter_ignores_disabled_sources(self) -> None:
        adapter = create_source_adapter(
            {
                "mode": {"source": "multi_source"},
                "sources": [
                    {"name": "disabled", "type": "rss", "url": "https://example.com", "enabled": False},
                    {"name": "enabled", "type": "rss", "url": "https://example.com/feed", "enabled": True},
                ],
            },
            session=FakeSession([FakeResponse(200, _rss_feed("AI", "https://example.com/a"))]),
        )

        self.assertIsInstance(adapter, CompositeSourceAdapter)
        self.assertEqual(len(adapter.adapters), 1)

    def test_create_source_adapter_online_mode_excludes_local_fixture_sources(self) -> None:
        adapter = create_source_adapter(
            {
                "mode": {"source": "online"},
                "sources": [
                    {
                        "name": "fixture",
                        "family": "local_fixture",
                        "type": "local_json",
                        "path": "data/raw/ai_news_raw.json",
                        "enabled": True,
                    },
                    {
                        "name": "enabled",
                        "type": "rss",
                        "url": "https://example.com/feed",
                        "enabled": True,
                    },
                ],
            },
            session=FakeSession([FakeResponse(200, _rss_feed("AI", "https://example.com/a"))]),
        )

        self.assertIsInstance(adapter, CompositeSourceAdapter)
        self.assertEqual(len(adapter.adapters), 1)
        self.assertIsInstance(adapter.adapters[0], RSSSourceAdapter)

    def test_create_source_adapter_local_fixture_mode_excludes_online_sources(self) -> None:
        adapter = create_source_adapter(
            {
                "mode": {"source": "local_fixture"},
                "sources": [
                    {
                        "name": "fixture",
                        "family": "local_fixture",
                        "type": "local_json",
                        "path": "data/raw/ai_news_raw.json",
                        "enabled": True,
                    },
                    {
                        "name": "enabled",
                        "type": "rss",
                        "url": "https://example.com/feed",
                        "enabled": True,
                    },
                ],
            },
            session=FakeSession([FakeResponse(200, _rss_feed("AI", "https://example.com/a"))]),
        )

        self.assertIsInstance(adapter, CompositeSourceAdapter)
        self.assertEqual(len(adapter.adapters), 1)
        self.assertIsInstance(adapter.adapters[0], LocalJsonSourceAdapter)

    def test_create_source_adapter_inherits_full_content_pipeline_config(self) -> None:
        adapter = create_source_adapter(
            {
                "mode": {"source": "multi_source"},
                "pipeline": {"fetch_full_content": True, "full_content_min_chars": 80},
                "sources": [
                    {
                        "name": "enabled",
                        "type": "rss",
                        "url": "https://example.com/feed",
                        "enabled": True,
                    }
                ],
            },
            session=FakeSession([FakeResponse(200, _rss_feed("AI", "https://example.com/a"))]),
        )

        self.assertIsInstance(adapter, CompositeSourceAdapter)
        child = adapter.adapters[0]
        self.assertIsInstance(child, RSSSourceAdapter)
        self.assertTrue(child.fetch_full_content)
        self.assertEqual(child.full_content_min_chars, 80)

    def test_create_source_adapter_supports_qbitai_rss_source(self) -> None:
        adapter = create_source_adapter(
            {
                "mode": {"source": "multi_source"},
                "sources": [
                    {
                        "name": "qbitai",
                        "family": "tech_media",
                        "type": "rss",
                        "url": "https://www.qbitai.com/feed",
                        "source": "量子位",
                        "source_type": "news",
                        "language": "zh",
                        "enabled": True,
                    }
                ],
            },
            session=FakeSession(
                [FakeResponse(200, _rss_feed("量子位：AI 芯片发布", "https://example.com/qbitai-chip"))]
            ),
        )

        self.assertIsInstance(adapter, CompositeSourceAdapter)
        self.assertEqual(len(adapter.adapters), 1)
        self.assertIsInstance(adapter.adapters[0], RSSSourceAdapter)

    def test_create_source_adapter_supports_ithome_rss_source(self) -> None:
        adapter = create_source_adapter(
            {
                "mode": {"source": "multi_source"},
                "sources": [
                    {
                        "name": "ithome_ai",
                        "family": "tech_media",
                        "type": "rss",
                        "url": "https://www.ithome.com/rss/",
                        "source": "IT之家",
                        "source_type": "news",
                        "language": "zh",
                        "enabled": True,
                    }
                ],
            },
            session=FakeSession(
                [FakeResponse(200, _rss_feed("IT之家：AI PC 市场继续升温", "https://example.com/ithome-ai"))]
            ),
        )

        self.assertIsInstance(adapter, CompositeSourceAdapter)
        self.assertEqual(len(adapter.adapters), 1)
        self.assertIsInstance(adapter.adapters[0], RSSSourceAdapter)

    def test_composite_source_skips_failed_source(self) -> None:
        adapter = CompositeSourceAdapter(
            [
                FailingSourceAdapter(),
                StaticSourceAdapter([_raw_payload("raw-good")]),
            ]
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "raw-good")
        self.assertEqual(len(adapter.errors), 1)
        self.assertEqual(adapter.source_metrics[0]["status"], "failed")
        self.assertEqual(adapter.source_metrics[1]["status"], "succeeded")

    def test_composite_source_marks_empty_and_partial_sources(self) -> None:
        github = GitHubReleasesSourceAdapter(
            {
                "name": "github_releases_ai_tools",
                "family": "official_channel",
                "type": "github_releases",
                "repos": ["example/repo-a", "example/repo-b"],
                "source": "GitHub Releases",
                "source_type": "release",
            },
            session=FakeSession(
                [
                    FakeResponse(
                        403,
                        {"message": "API rate limit exceeded"},
                        reason="Forbidden",
                    ),
                    FakeResponse(200, [_github_release(3, "repo-b", "v2.0.0")]),
                ]
            ),
        )
        adapter = CompositeSourceAdapter(
            [
                StaticSourceAdapter([]),
                github,
            ]
        )

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(adapter.source_metrics[0]["status"], "empty")
        self.assertEqual(adapter.source_metrics[1]["status"], "partial")
        self.assertEqual(adapter.source_metrics[1]["errors"][0]["category"], "rate_limit")

    def test_composite_source_marks_failed_http_429_as_rate_limited(self) -> None:
        rss = RSSSourceAdapter(
            {
                "name": "techcrunch_ai",
                "family": "tech_media",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "source": "TechCrunch",
                "source_type": "news",
                "retry_attempts": 1,
            },
            session=FakeSession(
                [
                    FakeResponse(
                        429,
                        "rate limited",
                        reason="Too Many Requests",
                    )
                ]
            ),
        )
        adapter = CompositeSourceAdapter([rss, StaticSourceAdapter([_raw_payload("raw-good")])])

        items = adapter.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(adapter.source_metrics[0]["status"], "rate_limited")
        self.assertEqual(adapter.source_metrics[0]["request_errors"][0]["category"], "rate_limited")

    def test_mock_llm_returns_result_metadata(self) -> None:
        adapter = MockLLMAdapter(["first", "second"], model="mock-test")

        first = adapter.generate("Summarize item")
        second = adapter.generate("Summarize another item")

        self.assertIsInstance(first, LLMResult)
        self.assertTrue(first.success)
        self.assertEqual(first.content, "first")
        self.assertEqual(second.content, "second")
        self.assertEqual(first.model, "mock-test")
        self.assertEqual(adapter.prompts, ["Summarize item", "Summarize another item"])
        self.assertGreater(first.total_tokens, 0)

    def test_mock_llm_validates_schema_response(self) -> None:
        response = {
            "id": "raw-1",
            "title": "OpenAI launches developer agents",
            "source": "OpenAI News",
            "url": "https://example.com/a",
            "published_at": "2026-05-20T10:00:00+00:00",
            "source_type": "blog",
            "language": "en",
            "summary": "OpenAI announced a model release for developer agents.",
            "content": "The release improves agent workflows.",
        }
        adapter = MockLLMAdapter([response])

        result = adapter.generate("Extract raw news", schema=RawNewsItem)

        self.assertTrue(result.success)
        self.assertIsInstance(result.parsed, RawNewsItem)
        self.assertEqual(result.parsed.id, "raw-1")
        self.assertIn("OpenAI launches developer agents", result.content)

    def test_create_llm_adapter_returns_mock_without_api_key(self) -> None:
        with _without_env("LLM_API_KEY"):
            adapter = create_llm_adapter({"llm": {"model": "real-model"}})

        self.assertIsInstance(adapter, MockLLMAdapter)

    def test_create_llm_adapter_requires_api_key_for_explicit_llm_mode(self) -> None:
        with _without_env("LLM_API_KEY"):
            with self.assertRaisesRegex(ValueError, "LLM_API_KEY is required"):
                create_llm_adapter(
                    {
                        "mode": {"llm": "llm"},
                        "llm": {"model": "real-model"},
                    }
                )

    def test_create_llm_adapter_returns_real_adapter_with_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "test-key",
                "LLM_MODEL": "env-model",
                "LLM_BASE_URL": "https://example.com/chat/completions",
            },
            clear=False,
        ):
            adapter = create_llm_adapter({})

        self.assertIsInstance(adapter, OpenAICompatibleLLMAdapter)
        self.assertEqual(adapter.model, "env-model")
        self.assertEqual(adapter.base_url, "https://example.com/chat/completions")

    def test_openai_compatible_adapter_returns_content_and_usage(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "choices": [
                            {"message": {"content": "Structured answer"}}
                        ],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 4,
                        },
                    },
                )
            ]
        )
        adapter = OpenAICompatibleLLMAdapter(
            "test-key",
            model="test-model",
            base_url="https://example.com/chat/completions",
            session=session,
        )

        result = adapter.generate("Summarize item")

        self.assertTrue(result.success)
        self.assertEqual(result.content, "Structured answer")
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.prompt_tokens, 12)
        self.assertEqual(result.completion_tokens, 4)
        self.assertEqual(result.total_tokens, 16)
        self.assertEqual(result.cost_usd, 0.0)
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(session.calls[0]["json"]["model"], "test-model")
        self.assertEqual(
            session.calls[0]["args"][0],
            "https://example.com/chat/completions",
        )

    def test_openai_compatible_adapter_estimates_cost_from_model_pricing(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "choices": [{"message": {"content": "Structured answer"}}],
                        "usage": {
                            "prompt_tokens": 1000,
                            "completion_tokens": 250,
                        },
                    },
                )
            ]
        )
        adapter = OpenAICompatibleLLMAdapter(
            "test-key",
            model="gpt-5-mini",
            base_url="https://example.com/chat/completions",
            session=session,
        )

        result = adapter.generate("Summarize item")

        self.assertTrue(result.success)
        self.assertEqual(result.cost_usd, 0.0015)

    def test_llm_pricing_uses_configured_model_price(self) -> None:
        pricing_table = pricing_table_from_config(
            {
                "OpenAI": {
                    "custom-model": {
                        "currency": "USD",
                        "unit_tokens": 1000,
                        "input_price": 0.01,
                        "output_price": 0.02,
                    }
                }
            }
        )

        cost = estimate_llm_cost_usd(
            provider="OpenAI",
            model="custom-model",
            prompt_tokens=500,
            completion_tokens=250,
            pricing_table=pricing_table,
        )

        self.assertEqual(cost, 0.01)
        pricing = find_model_pricing(
            provider="OpenAI",
            model="custom-model",
            pricing_table=pricing_table,
        )
        self.assertIsInstance(pricing, ModelPricing)
        self.assertEqual(pricing.input_price, 0.01)

    def test_create_llm_adapter_passes_configured_pricing(self) -> None:
        with patch.dict(os.environ, {"LLM_API_KEY": "test-key"}, clear=False):
            adapter = create_llm_adapter(
                {
                    "llm": {
                        "model": "custom-model",
                        "pricing": {
                            "OpenAI": {
                                "custom-model": {
                                    "unit_tokens": 1000,
                                    "input_price": 0.01,
                                    "output_price": 0.02,
                                }
                            }
                        },
                    }
                }
            )

        self.assertIsInstance(adapter, OpenAICompatibleLLMAdapter)
        cost = estimate_llm_cost_usd(
            provider=adapter.provider,
            model=adapter.model,
            prompt_tokens=500,
            completion_tokens=250,
            pricing_table=adapter.pricing_table,
        )
        self.assertEqual(cost, 0.01)

    def test_openai_compatible_adapter_accepts_sdk_style_base_url(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {"choices": [{"message": {"content": "Hi"}}]},
                )
            ]
        )
        adapter = OpenAICompatibleLLMAdapter(
            "test-key",
            base_url="https://ai.liaobots.work/v1",
            session=session,
        )

        result = adapter.generate("Hi")

        self.assertTrue(result.success)
        self.assertEqual(
            session.calls[0]["args"][0],
            "https://ai.liaobots.work/v1/chat/completions",
        )

    def test_openai_compatible_adapter_reports_http_error(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    429,
                    {"error": {"message": "rate limit exceeded"}},
                    reason="Too Many Requests",
                )
            ]
        )
        adapter = OpenAICompatibleLLMAdapter("test-key", session=session)

        result = adapter.generate("Summarize item")

        self.assertFalse(result.success)
        self.assertEqual(result.content, "")
        self.assertIn("HTTP 429", result.error or "")
        self.assertIn("rate limit exceeded", result.error or "")
        self.assertEqual(result.raw_response["error"]["message"], "rate limit exceeded")

    def test_openai_compatible_adapter_reports_network_error(self) -> None:
        session = FakeSession([requests.Timeout("request timed out")])
        adapter = OpenAICompatibleLLMAdapter("test-key", session=session)

        result = adapter.generate("Summarize item")

        self.assertFalse(result.success)
        self.assertEqual(result.content, "")
        self.assertIn("Timeout", result.error or "")


class StaticSourceAdapter(SourceAdapter):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.name = "static"

    def collect(self) -> list[dict[str, Any]]:
        return self.items


class FailingSourceAdapter(SourceAdapter):
    name = "failing"

    def collect(self) -> list[dict[str, Any]]:
        raise RuntimeError("source unavailable")


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any,
        *,
        reason: str = "",
        headers: dict[str, str] | None = None,
        url: str = "https://example.com/response",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.reason = reason
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = self.text.encode("utf-8")
        self.headers = headers or {}
        self.url = url

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}: {self.reason}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls.append({"args": args, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls.append({"args": args, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@contextmanager
def _without_env(*keys: str) -> Any:
    with patch.dict(os.environ, {}, clear=False):
        old_values = {key: os.environ.pop(key, None) for key in keys}
        try:
            yield
        finally:
            for key, value in old_values.items():
                if value is not None:
                    os.environ[key] = value


def _raw_payload(item_id: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": "OpenAI launches developer agents",
        "source": "OpenAI News",
        "url": "https://example.com/a",
        "published_at": "2026-05-20T10:00:00+00:00",
        "source_type": "blog",
        "language": "en",
        "summary": "OpenAI announced a model release for developer agents.",
        "content": "The release improves agent workflows.",
    }


def _rss_feed(
    title: str,
    link: str,
    published: str = "Wed, 20 May 2026 10:00:00 GMT",
    description: str = "AI summary <b>with markup</b>.",
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid>{link}</guid>
      <pubDate>{published}</pubDate>
      <description><![CDATA[{description}]]></description>
    </item>
  </channel>
</rss>
"""


def _rss_feed_two_items(
    *,
    first_title: str,
    first_link: str,
    first_description: str,
    second_title: str,
    second_link: str,
    second_description: str,
    published: str = "Wed, 20 May 2026 10:00:00 GMT",
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>{first_title}</title>
      <link>{first_link}</link>
      <guid>{first_link}</guid>
      <pubDate>{published}</pubDate>
      <description><![CDATA[{first_description}]]></description>
    </item>
    <item>
      <title>{second_title}</title>
      <link>{second_link}</link>
      <guid>{second_link}</guid>
      <pubDate>{published}</pubDate>
      <description><![CDATA[{second_description}]]></description>
    </item>
  </channel>
</rss>
"""


def _atom_feed(title: str, link: str, published: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>arXiv</title>
  <entry>
    <id>{link}</id>
    <title>{title}</title>
    <updated>{published}</updated>
    <published>{published}</published>
    <link href="{link}" />
    <link title="pdf" href="https://arxiv.org/pdf/2605.00001" rel="related" type="application/pdf" />
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:primary_category term="cs.AI" />
    <category term="cs.AI" />
    <category term="cs.LG" />
    <summary>Research summary for AI systems.</summary>
  </entry>
</feed>
"""


def _github_release(release_id: int, repo_name: str, tag_name: str) -> dict[str, Any]:
    return {
        "id": release_id,
        "name": tag_name,
        "tag_name": tag_name,
        "html_url": f"https://github.com/example/{repo_name}/releases/tag/{tag_name}",
        "published_at": "2026-05-20T10:00:00Z",
        "body": f"{repo_name} {tag_name} improves AI workloads.",
    }


def _article_html() -> str:
    return """
<!doctype html>
<html>
  <head>
    <title>AI Startup raises funding</title>
    <script>window.ads = true;</script>
  </head>
  <body>
    <nav>Subscribe to our newsletter</nav>
    <article>
      <h1>AI Startup raises funding</h1>
      <p>First full article paragraph explains the company, the product, and the funding round with enough detail to be useful for analysis.</p>
      <p>Second full article paragraph adds customer context, market impact, and operational details that were not present in the RSS summary.</p>
      <p>Third full article paragraph includes evidence about AI workflows, enterprise adoption, and deployment plans for the next quarter.</p>
    </article>
  </body>
</html>
"""


def _long_article_html() -> str:
    paragraphs = "\n".join(
        f"<p>Long full article paragraph {index} explains market context, product details, customer impact, and AI deployment evidence in enough detail for downstream extraction.</p>"
        for index in range(1, 8)
    )
    return f"""
<!doctype html>
<html>
  <body>
    <article>
      {paragraphs}
      <p>Final paragraph marker confirms that default raw collection keeps the complete article text without truncating it at collection time.</p>
    </article>
  </body>
</html>
"""


def _zh_article_html() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
  <body>
    <article class="post-content">
      <h1>量子位：大模型智能体进入企业落地阶段</h1>
      <p>企业开始把智能体系统真正接入客服、销售和研发流程，这意味着大模型应用从演示阶段转入业务流程重构阶段。</p>
      <p>多家厂商同时强调推理成本和模型稳定性，说明企业客户已经把可控性、可维护性和交付周期作为采购决策的重要依据。</p>
      <p>文章还提到自动化编排、知识库连接、多模态输入和私有化部署正在成为下一轮产品竞争的核心能力。</p>
    </article>
  </body>
</html>
"""


if __name__ == "__main__":
    unittest.main()
