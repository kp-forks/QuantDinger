"""
Polymarket预测市场API路由
提供预测市场数据和分析接口（只读，不涉及交易）
"""
from flask import Blueprint, jsonify, request, g

from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.data_sources.polymarket import PolymarketDataSource
from app.utils.db import get_db_connection
import json
from datetime import datetime, timedelta

logger = get_logger(__name__)

polymarket_bp = Blueprint('polymarket', __name__)

# 初始化服务
polymarket_source = PolymarketDataSource()


@polymarket_bp.route("/markets", methods=["GET"])
@login_required
def get_markets():
    """
    获取预测市场列表
    
    Query params:
        category: crypto/politics/economics/sports (optional)
        sort_by: volume_24h/ai_score/probability_change (default: volume_24h)
        limit: 数量 (default: 20)
    """
    try:
        category = request.args.get("category")
        sort_by = request.args.get("sort_by", "volume_24h")
        limit = int(request.args.get("limit", 20))
        
        # 获取市场列表
        markets = polymarket_source.get_trending_markets(category, limit * 2)
        
        logger.info(f"Fetched {len(markets)} markets from PolymarketDataSource (category={category}, limit={limit})")
        
        if not markets:
            logger.warning(f"No markets returned from PolymarketDataSource. This may indicate API issues or empty cache.")
            return jsonify({
                "code": 1,
                "msg": "success",
                "data": [],
                "warning": "No markets available. API may be unavailable or cache is empty."
            })
        
        # 从数据库读取缓存的AI分析结果（由后台任务批量分析生成）
        # 只读取30分钟内的分析结果
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                market_ids = [m.get('market_id') for m in markets if m.get('market_id')]
                
                if market_ids:
                    # 查询30分钟内的分析结果
                    from datetime import datetime, timedelta
                    cache_cutoff = datetime.now() - timedelta(minutes=30)
                    placeholders = ','.join(['%s'] * len(market_ids))
                    
                    cur.execute(f"""
                        SELECT market_id, ai_predicted_probability, market_probability, divergence,
                               recommendation, confidence_score, opportunity_score, reasoning, key_factors
                        FROM qd_polymarket_ai_analysis
                        WHERE market_id IN ({placeholders}) 
                          AND user_id IS NULL
                          AND created_at > %s
                        ORDER BY opportunity_score DESC
                    """, market_ids + [cache_cutoff])
                    
                    rows = cur.fetchall()
                    cur.close()
                    
                    # 构建分析结果映射
                    analysis_map = {}
                    for row in rows:
                        market_id = row.get('market_id')
                        if market_id:
                            key_factors_raw = row.get('key_factors')
                            key_factors = []
                            if key_factors_raw:
                                try:
                                    if isinstance(key_factors_raw, str):
                                        key_factors = json.loads(key_factors_raw)
                                    else:
                                        key_factors = key_factors_raw if isinstance(key_factors_raw, list) else []
                                except:
                                    key_factors = []
                            
                            analysis_map[market_id] = {
                                'predicted_probability': float(row.get('ai_predicted_probability') or 0),
                                'recommendation': row.get('recommendation') or 'HOLD',
                                'confidence_score': float(row.get('confidence_score') or 0),
                                'opportunity_score': float(row.get('opportunity_score') or 0),
                                'divergence': float(row.get('divergence') or 0),
                                'reasoning': row.get('reasoning') or '',
                                'key_factors': key_factors
                            }
                    
                    # 为每个市场添加分析结果
                    for market in markets:
                        market_id = market.get('market_id')
                        if market_id and market_id in analysis_map:
                            market['ai_analysis'] = analysis_map[market_id]
                        else:
                            market['ai_analysis'] = None
                else:
                    # 如果没有市场ID，设置分析为None
                    for market in markets:
                        market['ai_analysis'] = None
                        
        except Exception as e:
            logger.warning(f"Failed to load cached analysis: {e}")
            # 出错时，所有市场都没有分析结果
            for market in markets:
                market['ai_analysis'] = None
        
        # 筛选：优先返回有交易机会的市场，但也包含其他活跃市场
        # 重点：不是简单复制数据，而是找到交易机会，但也要保证有足够的数据展示
        opportunity_markets = []
        other_markets = []
        
        for market in markets:
            ai_analysis = market.get('ai_analysis')
            volume = market.get('volume_24h', 0) or 0
            prob = market.get('current_probability', 50.0) or 50.0
            
            if ai_analysis:
                opportunity_score = ai_analysis.get('opportunity_score', 0) or 0
                divergence = abs(ai_analysis.get('divergence', 0) or 0)
                confidence = ai_analysis.get('confidence_score', 0) or 0
                
                # 筛选条件：机会评分>60 或 (差异>15% 且 置信度>70) 或 (差异>10% 且 置信度>60)
                if opportunity_score > 60 or (divergence > 15 and confidence > 70) or (divergence > 10 and confidence > 60):
                    opportunity_markets.append(market)
                elif volume > 5000:  # 交易量较大的也作为备选
                    other_markets.append(market)
            else:
                # 如果没有AI分析，但交易量大且概率不是50%（说明有明确的市场共识），也包含
                if volume > 5000 and abs(prob - 50.0) > 5:  # 降低阈值，包含更多市场
                    other_markets.append(market)
        
        # 合并结果：优先显示机会市场，然后补充其他活跃市场
        if opportunity_markets:
            # 如果有机会市场，优先显示它们，然后补充其他市场直到达到limit
            result_markets = opportunity_markets[:limit]
            remaining = limit - len(result_markets)
            if remaining > 0 and other_markets:
                result_markets.extend(other_markets[:remaining])
            opportunity_markets = result_markets
        elif other_markets:
            # 如果没有机会市场，至少返回高交易量的市场
            opportunity_markets = other_markets[:limit]
        else:
            # 如果都没有，返回原始市场列表的前几个
            opportunity_markets = markets[:min(limit, len(markets))]
        
        # 排序和筛选
        if sort_by == "ai_score":
            # 按AI机会评分排序（高概率/高回报比优先）
            opportunity_markets.sort(
                key=lambda x: (
                    x.get('ai_analysis', {}).get('opportunity_score', 0) or 0,
                    abs(x.get('ai_analysis', {}).get('divergence', 0) or 0),  # 差异越大越好
                    x.get('ai_analysis', {}).get('confidence_score', 0) or 0  # 置信度越高越好
                ),
                reverse=True
            )
        elif sort_by == "high_probability":
            # 高概率机会：AI预测概率 > 市场概率 + 10%
            opportunity_markets.sort(
                key=lambda x: (
                    x.get('ai_analysis', {}).get('ai_predicted_probability', 0) or 0,
                    x.get('ai_analysis', {}).get('confidence_score', 0) or 0
                ),
                reverse=True
            )
        elif sort_by == "high_return":
            # 高回报比机会：AI与市场差异大且置信度高
            opportunity_markets.sort(
                key=lambda x: (
                    abs(x.get('ai_analysis', {}).get('divergence', 0) or 0) * 
                    (x.get('ai_analysis', {}).get('confidence_score', 0) or 0) / 100,
                    x.get('ai_analysis', {}).get('opportunity_score', 0) or 0
                ),
                reverse=True
            )
        elif sort_by == "probability_change":
            # 需要历史数据，暂时按volume排序
            opportunity_markets.sort(key=lambda x: x.get('volume_24h', 0), reverse=True)
        else:
            opportunity_markets.sort(key=lambda x: x.get('volume_24h', 0), reverse=True)
        
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": opportunity_markets[:limit],
            "total_opportunities": len(opportunity_markets),
            "total_markets": len(markets)
        })
        
    except Exception as e:
        logger.error(f"get_markets failed: {e}", exc_info=True)
        return jsonify({
            "code": 0,
            "msg": str(e),
            "data": None
        }), 500


@polymarket_bp.route("/markets/<market_id>", methods=["GET"])
@login_required
def get_market_detail(market_id: str):
    """
    获取单个市场详情和AI分析
    
    支持通过market ID或slug查询
    """
    try:
        # 确保market_id是字符串
        market_id = str(market_id).strip()
        # 获取市场数据
        market = polymarket_source.get_market_details(market_id)
        if not market:
            return jsonify({
                "code": 0,
                "msg": "Market not found",
                "data": None
            }), 404
        
        # 从数据库读取缓存的AI分析结果（30分钟内）
        analysis = None
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cache_cutoff = datetime.now() - timedelta(minutes=30)
                
                cur.execute("""
                    SELECT ai_predicted_probability, market_probability, divergence,
                           recommendation, confidence_score, opportunity_score,
                           reasoning, key_factors, related_assets, created_at
                    FROM qd_polymarket_ai_analysis
                    WHERE market_id = %s AND user_id IS NULL AND created_at > %s
                    ORDER BY created_at DESC LIMIT 1
                """, (market_id, cache_cutoff))
                
                row = cur.fetchone()
                cur.close()
                
                if row:
                    key_factors_raw = row.get('key_factors')
                    key_factors = []
                    if key_factors_raw:
                        try:
                            if isinstance(key_factors_raw, str):
                                key_factors = json.loads(key_factors_raw)
                            else:
                                key_factors = key_factors_raw if isinstance(key_factors_raw, list) else []
                        except:
                            key_factors = []
                    
                    analysis = {
                        "ai_predicted_probability": float(row.get('ai_predicted_probability') or 0),
                        "market_probability": float(row.get('market_probability') or 0),
                        "divergence": float(row.get('divergence') or 0),
                        "recommendation": row.get('recommendation') or 'HOLD',
                        "confidence_score": float(row.get('confidence_score') or 0),
                        "opportunity_score": float(row.get('opportunity_score') or 0),
                        "reasoning": row.get('reasoning') or '',
                        "key_factors": key_factors,
                        "related_assets": row.get('related_assets') if row.get('related_assets') else []
                    }
        except Exception as e:
            logger.warning(f"Failed to load cached analysis for market {market_id}: {e}")
        
        # 资产交易机会（暂时返回空，可以后续实现）
        asset_opportunities = []
        
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {
                "market": market,
                "ai_analysis": analysis,
                "asset_opportunities": asset_opportunities
            }
        })
        
    except Exception as e:
        logger.error(f"get_market_detail failed: {e}", exc_info=True)
        return jsonify({
            "code": 0,
            "msg": str(e),
            "data": None
        }), 500


@polymarket_bp.route("/markets/<market_id>/opportunities", methods=["GET"])
@login_required
def get_market_opportunities(market_id: str):
    """获取基于该预测市场的资产交易机会（暂时返回空，后续可扩展）"""
    try:
        # 暂时返回空列表，可以后续实现基于预测市场的资产推荐
        opportunities = []
        
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": opportunities
        })
        
    except Exception as e:
        logger.error(f"get_market_opportunities failed: {e}", exc_info=True)
        return jsonify({
            "code": 0,
            "msg": str(e),
            "data": None
        }), 500


@polymarket_bp.route("/recommendations", methods=["GET"])
@login_required
def get_recommendations():
    """
    获取AI推荐的高价值预测市场
    
    Query params:
        limit: 数量 (default: 10)
    """
    try:
        limit = int(request.args.get("limit", 10))
        
        # 获取所有活跃市场
        all_markets = polymarket_source.get_trending_markets(limit=100)
        
        # 从数据库读取缓存的AI分析结果（30分钟内，按机会评分排序）
        recommendations = []
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cache_cutoff = datetime.now() - timedelta(minutes=30)
                market_ids = [m.get('market_id') for m in all_markets if m.get('market_id')]
                
                if market_ids:
                    placeholders = ','.join(['%s'] * len(market_ids))
                    cur.execute(f"""
                        SELECT a.market_id, a.opportunity_score, a.recommendation, 
                               a.confidence_score, a.reasoning, a.key_factors,
                               m.question, m.current_probability, m.volume_24h, m.category
                        FROM qd_polymarket_ai_analysis a
                        JOIN qd_polymarket_markets m ON a.market_id = m.market_id
                        WHERE a.market_id IN ({placeholders})
                          AND a.user_id IS NULL
                          AND a.created_at > %s
                          AND a.opportunity_score > 70
                        ORDER BY a.opportunity_score DESC
                        LIMIT %s
                    """, market_ids + [cache_cutoff, limit])
                    
                    rows = cur.fetchall()
                    cur.close()
                    
                    for row in rows:
                        key_factors_raw = row.get('key_factors')
                        key_factors = []
                        if key_factors_raw:
                            try:
                                if isinstance(key_factors_raw, str):
                                    key_factors = json.loads(key_factors_raw)
                                else:
                                    key_factors = key_factors_raw if isinstance(key_factors_raw, list) else []
                            except:
                                key_factors = []
                        
                        recommendations.append({
                            "market_id": row.get('market_id'),
                            "question": row.get('question'),
                            "current_probability": float(row.get('current_probability') or 0),
                            "volume_24h": float(row.get('volume_24h') or 0),
                            "category": row.get('category'),
                            "ai_analysis": {
                                "opportunity_score": float(row.get('opportunity_score') or 0),
                                "recommendation": row.get('recommendation') or 'HOLD',
                                "confidence_score": float(row.get('confidence_score') or 0),
                                "reasoning": row.get('reasoning') or '',
                                "key_factors": key_factors
                            }
                        })
        except Exception as e:
            logger.warning(f"Failed to load recommendations: {e}")
        
        # 如果没有缓存结果，返回空列表（等待后台任务分析）
        
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": recommendations[:limit]
        })
        
    except Exception as e:
        logger.error(f"get_recommendations failed: {e}", exc_info=True)
        return jsonify({
            "code": 0,
            "msg": str(e),
            "data": None
        }), 500


@polymarket_bp.route("/search", methods=["GET"])
@login_required
def search_markets():
    """搜索预测市场"""
    try:
        keyword = request.args.get("q", "").strip()
        if not keyword:
            return jsonify({
                "code": 0,
                "msg": "Keyword required",
                "data": None
            }), 400
        
        limit = int(request.args.get("limit", 20))
        
        markets = polymarket_source.search_markets(keyword, limit)
        
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": markets
        })
        
    except Exception as e:
        logger.error(f"search_markets failed: {e}", exc_info=True)
        return jsonify({
            "code": 0,
            "msg": str(e),
            "data": None
        }), 500
