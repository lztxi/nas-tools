import re

import cn2an

import log
from config import Config
from app.message import Message
from app.media.douban import DouBan
from app.downloader import Downloader
from app.searcher import Searcher
from app.utils import StringUtils
from app.media import MetaInfo, Media
from app.helper import DbHelper, ProgressHelper
from app.utils.types import SearchType, MediaType
from app.subscribe import Subscribe

SEARCH_MEDIA_CACHE = []
SEARCH_MEDIA_TYPE = "SEARCH"


def search_medias_for_web(content, ident_flag=True, filters=None, tmdbid=None, media_type=None):
    """
    WEB资源搜索
    :param content: 关键字文本，可以包括 类型、标题、季、集、年份等信息，使用 空格分隔，也支持种子的命名格式
    :param ident_flag: 是否进行媒体信息识别
    :param filters: 其它过滤条件
    :param tmdbid: TMDBID或DB:豆瓣ID
    :param media_type: 媒体类型，配合tmdbid传入
    :return: 错误码，错误原因，成功时直接插入数据库
    """
    _dbhelper = DbHelper()
    mtype, key_word, season_num, episode_num, year, content = StringUtils.get_keyword_from_string(content)
    if not key_word:
        log.info("【Web】%s 检索关键字有误！" % content)
        return -1, "%s 未识别到搜索关键字！" % content
    # 开始进度
    search_process = ProgressHelper()
    search_process.start('search')
    # 识别媒体
    media_info = None
    if ident_flag:
        # 有TMDBID或豆瓣ID
        if tmdbid:
            # 豆瓣ID
            if tmdbid.startswith("DB:"):
                # 以豆瓣ID查询
                doubanid = tmdbid[3:]
                # 先从网页抓取（含TMDBID）
                doubaninfo = DouBan().get_media_detail_from_web(doubanid)
                if not doubaninfo or not doubaninfo.get("imdbid"):
                    # 从API抓取
                    doubaninfo = DouBan().get_douban_detail(doubanid=doubanid, mtype=media_type)
                    if not doubaninfo:
                        return -1, "%s 查询不到豆瓣信息，请确认网络是否正常！" % content
                if doubaninfo.get("imdbid"):
                    # 按IMDBID查询TMDB
                    tmdbid = Media().get_tmdbid_by_imdbid(doubaninfo.get("imdbid"))
                    if tmdbid:
                        # 以TMDBID查询
                        media_info = MetaInfo(mtype=media_type or mtype, title=content)
                        media_info.set_tmdb_info(Media().get_tmdb_info(mtype=media_type or mtype, tmdbid=tmdbid))
                        media_info.imdb_id = doubaninfo.get("imdbid")
                        if doubaninfo.get("season") and str(doubaninfo.get("season")).isdigit():
                            media_info.begin_season = int(doubaninfo.get("season"))
                if not media_info or not media_info.tmdb_info:
                    # 按豆瓣名称查
                    title = doubaninfo.get("title")
                    media_info = Media().get_media_info(mtype=media_type,
                                                        title="%s %s" % (title, doubaninfo.get("year")),
                                                        strict=True)
                    # 整合集
                    if media_info and episode_num:
                        media_info.begin_episode = int(episode_num)
            # TMDBID
            else:
                # 以TMDBID查询
                media_info = MetaInfo(mtype=media_type or mtype, title=content)
                media_info.set_tmdb_info(Media().get_tmdb_info(mtype=media_type or mtype, tmdbid=tmdbid))
        else:
            # 按输入名称查
            media_info = Media().get_media_info(mtype=media_type or mtype, title=content)

        if media_info and media_info.tmdb_info:
            log.info(f"【Web】从TMDB中匹配到{media_info.type.value}：{media_info.get_title_string()}")
            # 查找的季
            if media_info.begin_season is None:
                search_season = None
            else:
                search_season = media_info.get_season_list()
            # 查找的集
            search_episode = media_info.get_episode_list()
            if search_episode and not search_season:
                search_season = [1]
            # 中文名
            if media_info.cn_name:
                search_cn_name = media_info.cn_name
            else:
                search_cn_name = media_info.title
            # 英文名
            search_en_name = None
            if media_info.en_name:
                search_en_name = media_info.en_name
            else:
                if media_info.original_language == "en":
                    search_en_name = media_info.original_title
                else:
                    en_info = Media().get_tmdb_info(mtype=media_info.type, tmdbid=media_info.tmdb_id, language="en-US")
                    if en_info:
                        search_en_name = en_info.get("title") if media_info.type == MediaType.MOVIE else en_info.get(
                            "name")
            # 两次搜索名称
            second_search_name = None
            if Config().get_config("laboratory").get("search_en_title"):
                if search_en_name:
                    first_search_name = search_en_name
                    second_search_name = search_cn_name
                else:
                    first_search_name = search_cn_name
            else:
                first_search_name = search_cn_name
                if search_en_name:
                    second_search_name = search_en_name

            filter_args = {"season": search_season,
                           "episode": search_episode,
                           "year": media_info.year,
                           "type": media_info.type}
        else:
            # 查询不到数据，使用快速搜索
            log.info(f"【Web】{content} 未从TMDB匹配到媒体信息，将使用快速搜索...")
            ident_flag = False
            media_info = None
            first_search_name = key_word
            second_search_name = None
            filter_args = {"season": season_num,
                           "episode": episode_num,
                           "year": year}
    # 快速搜索
    else:
        first_search_name = key_word
        second_search_name = None
        filter_args = {"season": season_num,
                       "episode": episode_num,
                       "year": year}
    # 整合高级查询条件
    if filters:
        filter_args.update(filters)
    # 开始检索
    log.info("【Web】开始检索 %s ..." % content)
    media_list = Searcher().search_medias(key_word=first_search_name,
                                          filter_args=filter_args,
                                          match_type=1 if ident_flag else 2,
                                          match_media=media_info,
                                          in_from=SearchType.WEB)
    # 使用第二名称重新搜索
    if ident_flag \
            and len(media_list) == 0 \
            and second_search_name \
            and second_search_name != first_search_name:
        search_process.start('search')
        search_process.update(ptype='search',
                              text="%s 未检索到资源,尝试通过 %s 重新检索 ..." % (first_search_name, second_search_name))
        log.info("【Searcher】%s 未检索到资源,尝试通过 %s 重新检索 ..." % (first_search_name, second_search_name))
        media_list = Searcher().search_medias(key_word=second_search_name,
                                              filter_args=filter_args,
                                              match_type=1,
                                              match_media=media_info,
                                              in_from=SearchType.WEB)
    # 清空缓存结果
    _dbhelper.delete_all_search_torrents()
    # 结束进度
    search_process.end('search')
    if len(media_list) == 0:
        log.info("【Web】%s 未检索到任何资源" % content)
        return 1, "%s 未检索到任何资源" % content
    else:
        log.info("【Web】共检索到 %s 个有效资源" % len(media_list))
        # 插入数据库
        media_list = sorted(media_list, key=lambda x: "%s%s%s" % (str(x.res_order).rjust(3, '0'),
                                                                  str(x.site_order).rjust(3, '0'),
                                                                  str(x.seeders).rjust(10, '0')), reverse=True)
        _dbhelper.insert_search_results(media_list)
        return 0, ""


def search_media_by_message(input_str, in_from: SearchType, user_id=None):
    """
    输入字符串，解析要求并进行资源检索
    :param input_str: 输入字符串，可以包括标题、年份、季、集的信息，使用空格隔开
    :param in_from: 搜索下载的请求来源
    :param user_id: 需要发送消息的，传入该参数，则只给对应用户发送交互消息
    :return: 请求的资源是否全部下载完整、请求的文本对应识别出来的媒体信息、请求的资源如果是剧集，则返回下载后仍然缺失的季集信息
    """
    global SEARCH_MEDIA_TYPE
    global SEARCH_MEDIA_CACHE

    if not input_str:
        log.info("【Searcher】检索关键字有误！")
        return
    # 如果是数字，表示选择项
    if input_str.isdigit() and int(input_str) < 10:
        # 获取之前保存的可选项
        choose = int(input_str) - 1
        if choose < 0 or choose >= len(SEARCH_MEDIA_CACHE):
            Message().send_channel_msg(channel=in_from,
                                       title="输入有误！",
                                       user_id=user_id)
            log.warn("【Web】错误的输入值：%s" % input_str)
            return
        media_info = SEARCH_MEDIA_CACHE[choose]
        if SEARCH_MEDIA_TYPE == "SEARCH":
            # 如果是豆瓣数据，需要重新查询TMDB的数据
            if media_info.douban_id:
                _title = media_info.get_title_string()
                # 先从网页抓取（含TMDBID）
                doubaninfo = DouBan().get_media_detail_from_web(media_info.douban_id)
                if doubaninfo and doubaninfo.get("imdbid"):
                    tmdbid = Media().get_tmdbid_by_imdbid(doubaninfo.get("imdbid"))
                    if tmdbid:
                        # 按IMDBID查询TMDB
                        media_info.set_tmdb_info(Media().get_tmdb_info(mtype=media_info.type, tmdbid=tmdbid))
                        media_info.imdb_id = doubaninfo.get("imdbid")
                else:
                    search_episode = media_info.begin_episode
                    media_info = Media().get_media_info(title="%s %s" % (media_info.title, media_info.year),
                                                        mtype=media_info.type,
                                                        strict=True)
                    media_info.begin_episode = search_episode
                if not media_info or not media_info.tmdb_info:
                    Message().send_channel_msg(channel=in_from,
                                               title="%s 从TMDB查询不到媒体信息！" % _title,
                                               user_id=user_id)
                    return
            # 搜索
            __search_media(in_from, media_info, user_id)
        else:
            # 订阅
            __rss_media(in_from, media_info, user_id)
    # 接收到文本，开始查询可能的媒体信息供选择
    else:
        if input_str.startswith("订阅"):
            SEARCH_MEDIA_TYPE = "RSS"
            input_str = re.sub(r"订阅[:：\s]*", "", input_str)
        else:
            SEARCH_MEDIA_TYPE = "SEARCH"

        # 去掉查询中的电影或电视剧关键字
        mtype, _, _, _, _, content = StringUtils.get_keyword_from_string(input_str)
        # 识别媒体信息，列出匹配到的所有媒体
        log.info("【Web】正在识别 %s 的媒体信息..." % content)
        media_info = MetaInfo(title=content, mtype=mtype)
        if not media_info.get_name():
            Message().send_channel_msg(channel=in_from,
                                       title="无法识别搜索内容！",
                                       user_id=user_id)
            return
        # 搜索名称
        use_douban_titles = Config().get_config("laboratory").get("use_douban_titles")
        if use_douban_titles:
            tmdb_infos = DouBan().search_douban_medias(
                keyword=media_info.get_name() if not media_info.year else "%s %s" % (
                    media_info.get_name(), media_info.year),
                mtype=mtype,
                num=6,
                season=media_info.begin_season,
                episode=media_info.begin_episode)
        else:
            tmdb_infos = Media().get_tmdb_infos(title=media_info.get_name(), year=media_info.year, mtype=mtype)
        if not tmdb_infos:
            # 查询不到媒体信息
            Message().send_channel_msg(channel=in_from,
                                       title="%s 查询不到媒体信息！" % content,
                                       user_id=user_id)
            return

        # 保存识别信息到临时结果中
        SEARCH_MEDIA_CACHE.clear()
        if use_douban_titles:
            SEARCH_MEDIA_CACHE = tmdb_infos
        else:
            for tmdb_info in tmdb_infos:
                meta_info = MetaInfo(title=content)
                meta_info.set_tmdb_info(tmdb_info)
                if meta_info.begin_season:
                    meta_info.title = "%s 第%s季" % (meta_info.title, cn2an.an2cn(meta_info.begin_season, mode='low'))
                if meta_info.begin_episode:
                    meta_info.title = "%s 第%s集" % (meta_info.title, meta_info.begin_episode)
                SEARCH_MEDIA_CACHE.append(meta_info)

        if 1 == len(SEARCH_MEDIA_CACHE):
            # 只有一条数据，直接开始搜索
            media_info = SEARCH_MEDIA_CACHE[0]
            if SEARCH_MEDIA_TYPE == "SEARCH":
                # 如果是豆瓣数据，需要重新查询TMDB的数据
                if media_info.douban_id:
                    _title = media_info.get_title_string()
                    media_info = Media().get_media_info(title="%s %s" % (media_info.title, media_info.year),
                                                        mtype=media_info.type, strict=True)
                    if not media_info or not media_info.tmdb_info:
                        Message().send_channel_msg(channel=in_from,
                                                   title="%s 从TMDB查询不到媒体信息！" % _title,
                                                   user_id=user_id)
                        return
                # 发送消息
                Message().send_channel_msg(channel=in_from,
                                           title=media_info.get_title_vote_string(),
                                           text=media_info.get_overview_string(),
                                           image=media_info.get_message_image(),
                                           user_id=user_id)
                # 开始搜索
                __search_media(in_from, media_info, user_id)
            else:
                # 添加订阅
                __rss_media(in_from, media_info, user_id)
        else:
            # 发送消息通知选择
            Message().send_channel_list_msg(channel=in_from,
                                            title="共找到%s条相关信息，请回复对应序号" % len(SEARCH_MEDIA_CACHE),
                                            medias=SEARCH_MEDIA_CACHE,
                                            user_id=user_id)


def __search_media(in_from, media_info, user_id):
    """
    开始搜索和发送消息
    """
    # 检查是否存在，电视剧返回不存在的集清单
    exist_flag, no_exists, messages = Downloader().check_exists_medias(meta_info=media_info)
    if messages:
        Message().send_channel_msg(channel=in_from,
                                   title="\n".join(messages),
                                   user_id=user_id)
    # 已经存在
    if exist_flag:
        return

    # 开始检索
    Message().send_channel_msg(channel=in_from,
                               title="开始检索 %s ..." % media_info.title,
                               user_id=user_id)
    search_result, no_exists, search_count, download_count = Searcher().search_one_media(media_info=media_info,
                                                                                         in_from=in_from,
                                                                                         no_exists=no_exists)
    # 没有搜索到数据
    if not search_count:
        Message().send_channel_msg(channel=in_from,
                                   title="%s 未搜索到任何资源" % media_info.title,
                                   user_id=user_id)
    else:
        # 搜索到了但是没开自动下载
        if download_count is None:
            Message().send_channel_msg(channel=in_from,
                                       title="%s 共搜索到%s个资源，点击选择下载" % (media_info.title, search_count),
                                       image=media_info.get_message_image(),
                                       url="search",
                                       user_id=user_id)
            return
        else:
            # 搜索到了但是没下载到数据
            if download_count == 0:
                Message().send_channel_msg(channel=in_from,
                                           title="%s 共搜索到%s个结果，但没有下载到任何资源" % (media_info.title, search_count),
                                           user_id=user_id)
    # 没有下载完成，且打开了自动添加订阅
    if not search_result and Config().get_config('pt').get('search_no_result_rss'):
        # 添加订阅
        __rss_media(in_from=in_from, media_info=media_info, user_id=user_id, state='R')


def __rss_media(in_from, media_info, user_id=None, state='D'):
    """
    开始添加订阅和发送消息
    """
    # 添加订阅
    if media_info.douban_id:
        code, msg, media_info = Subscribe().add_rss_subscribe(media_info.type,
                                                              media_info.title,
                                                              media_info.year,
                                                              media_info.begin_season,
                                                              doubanid=media_info.douban_id,
                                                              state=state)
    else:
        code, msg, media_info = Subscribe().add_rss_subscribe(media_info.type,
                                                              media_info.title,
                                                              media_info.year,
                                                              media_info.begin_season,
                                                              tmdbid=media_info.tmdb_id,
                                                              state=state)
    if code == 0:
        log.info("【Web】%s %s 已添加订阅" % (media_info.type.value, media_info.get_title_string()))
        if in_from in [SearchType.WX, SearchType.TG]:
            Message().send_rss_success_message(in_from=in_from, media_info=media_info, user_id=user_id)
    else:
        if in_from in [SearchType.WX, SearchType.TG]:
            log.info("【Web】%s 添加订阅失败：%s" % (media_info.title, msg))
            Message().send_channel_msg(channel=in_from,
                                       title="%s 添加订阅失败：%s" % (media_info.title, msg),
                                       user_id=user_id)
