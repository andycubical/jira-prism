# -*- coding: utf-8 -*-
#
####################################################
#
# PRISM - Pipeline for animation and VFX projects
#
# www.prism-pipeline.com
#
# contact: contact@prism-pipeline.com
#
####################################################
#
#
# Copyright (C) 2016-2023 Richard Frangenberg
# Copyright (C) 2023 Prism Software GmbH
#
# Licensed under proprietary license. See license file in the directory of this plugin for details.
#
# This file is part of Prism-Plugin-Kitsu.
#
# Prism-Plugin-Kitsu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

aiohttp = None
import asyncio
import json
import os
import base64
import sys
import re
import requests
import importlib
import logging
import traceback
import tempfile
import time
import uuid
from datetime import datetime

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from PrismUtils.Decorators import err_catcher_plugin as err_catcher


logger = logging.getLogger(__name__)


class Prism_Jira_Functions(object):
    def __init__(self, core, plugin):
        # TODO cleanup
        self.core = core
        self.plugin = plugin
        if self.isActive():
            extModPath = os.path.join(self.pluginDirectory, "ExternalModules")
            cpModPath = os.path.join(extModPath, "CrossPlatform")
            if cpModPath not in sys.path:
                sys.path.append(cpModPath)

            if sys.version[0] == "2":
                cpModPath = os.path.join(extModPath, "Python27")

                if cpModPath not in sys.path:
                    sys.path.append(cpModPath)
            elif sys.version[0] == "3":
                cpModPath = os.path.join(extModPath, "Python3")

                if cpModPath not in sys.path:
                    sys.path.append(cpModPath)

            self.name = "Jira"
            self.dbCache = {}
            
            self.requestInProgress = False

            self.requiresLogin = True
            self.hasRemoteDatabase = True
            self.hasNotes = True
            self.canAddNotesToVersions = False
            self.hasTaskAssignment = True
            self.allowLoginRequest = True
            self.allowUrlRequest = True
            
            global aiohttp
            aiohttp = importlib.import_module("aiohttp")

            self.register()

    # if returns true, the plugin will be loaded by Prism
    @err_catcher(name=__name__)
    def isActive(self):
        return True

    @property
    def jiraApi(self):
        if not hasattr(self, "_jiraApi"):
            self._jiraApi = importlib.import_module("jira")

        return self._jiraApi

    @err_catcher(name=__name__)
    def register(self):
        self.prjMng = self.core.getPlugin("ProjectManagement")
        if not self.prjMng:
            self.core.registerCallback("pluginLoaded", self.onPluginLoaded, plugin=self.plugin)
            return

        self.core.registerCallback("onWriteNoteWidgetCreated", self.onWriteNoteWidgetCreated, plugin=self.plugin)
        self.prjMng.registerManager(self)
        self.core.registerCallback("onProjectCreationSettingsReloaded", self.onProjectCreationSettingsReloaded, plugin=self.plugin)

    @err_catcher(name=__name__)
    def unregister(self):
        if getattr(self, "prjMng"):
            self.prjMng.unregisterManager(self.name)

    @err_catcher(name=__name__)
    def onPluginLoaded(self, plugin):
        if plugin.pluginName == "ProjectManagement":
            self.register()

    @property    
    def canCreateAssets(self):
        return self.getAllowAssetCreation()

    @property    
    def canCreateShots(self):
        return self.getAllowShotCreation()

    @err_catcher(name=__name__)
    def postInstall(self):
        # TODO
        if self.core.getPlugin("ProjectManagement"):
            return

        msg = "To use the Kitsu plugin you need to install the \"Project Management\" plugin.\n\nDo you want to install the \"Project Management\" plugin now?"
        result = self.core.popupQuestion(msg)
        if result == "Yes":
            if self.core.getPlugin("PrismInternals"):
                self.core.getPlugin("PrismInternals").internals.installPlugin("ProjectManagement")

    @err_catcher(name=__name__)
    def onProjectCreationSettingsReloaded(self, settings):
        if "prjManagement" not in settings:
            return

        if "jira_components" not in settings["prjManagement"]:
            return
        
        if "jira_projectKey" not in settings["prjManagement"]:
            return
        
        del settings["prjManagement"]["jira_components"]["jira_projectKey"]

    @err_catcher(name=__name__)
    def getRequiredAuthorization(self):
        data = [
            {"name": "jira_username", "label": "Username", "isSecret": False, "type": "QLineEdit"},
            {"name": "jira_apiToken", "label": "API Token", "isSecret": True, "type": "QLineEdit"}
        ]
        return data

    @err_catcher(name=__name__)
    def getIconPath(self):
        path = os.path.join(self.pluginDirectory, "Resources", "jira.png")
        return path

    @err_catcher(name=__name__)
    def getIcon(self):
        path = self.getIconPath()
        return QPixmap(path)

    @err_catcher(name=__name__)
    def getLoginName(self):
        # TODO
        data = self.prjMng.getAuthorization() or {}
        return data.get("jira_username")

    @err_catcher(name=__name__)
    def getAllUsernames(self):
        # TODO
        text = "Querying usernames - please wait..."
        popup = self.core.waitPopup(self.core, text, hidden=True)
        with popup:
            unfilteredUsers = self.makeDbRequest("search_users", ['query="."', 'includeInactive=False'])
            users = [user.displayName for user in unfilteredUsers if user.accountType=="atlassian"]

        loginName = self.getUsername()
        if loginName not in users:
            users.insert(0, loginName)

        return users

    @err_catcher(name=__name__)
    def getUsers(self):
        prjId = self.getCurrentProjectId()
        if prjId is None:
            return
        text = "Querying usernames - please wait..."
        popup = self.core.waitPopup(self.core, text, hidden=True)
        with popup:
            unfilteredUsers = self.makeDbRequest("search_users", ['query="."', 'includeInactive=False'])
            jiraUsers = [user.displayName for user in unfilteredUsers if user.accountType=="atlassian"]

        users = []
        for jiraUser in jiraUsers:
            userData = {
                "name": jiraUser.displayName,
                "abbreviation": self.core.users.getUserAbbreviation(userName=jiraUser.displayName, fromConfig=False),
                "role": "artist",
                # "projects": projects,
            }
            users.append(userData)

        return users

    @err_catcher(name=__name__)
    def getDefaultStatus(self):
        # TODO
        dftStatus = [
            {
                "name": "Todo",
                "abbreviation": "todo",
                "color": [95, 98, 106],
                "tasks": True,
                "products": True,
                "media": True,
            },
            {
                "name": "Work In Progress",
                "abbreviation": "wip",
                "color": [50, 115, 220],
                "tasks": True,
                "products": True,
                "media": True,
            },
            {
                "name": "Waiting For Approval",
                "abbreviation": "wfa",
                "color": [171, 38, 255],
                "tasks": True,
                "products": True,
                "media": True,
            },
            {
                "name": "Retake",
                "abbreviation": "retake",
                "color": [255, 56, 96],
                "tasks": True,
                "products": True,
                "media": True,
            },
            {
                "name": "Done",
                "abbreviation": "done",
                "color": [34, 209, 96],
                "tasks": True,
                "products": True,
                "media": True,
            },
        ]
        return dftStatus

    @err_catcher(name=__name__)
    def getProjectSettings(self):
        # TODO
        data = [
            {"name": "jira_setup", "label": "Setup...", "tooltip": "Opens a setup window to guide you through the process of connecting your Kitsu project to your Prism project.", "type": "QPushButton", "callback": self.prjMng.openSetupDlg},
            {"name": "jira_url", "label": "Url", "type": "QLineEdit"},
            {"name": "jira_projectKey", "label": "Project Key", "type": "QLineEdit"},
            {"name": "jira_components", "label": "Components", "type": "QLineEdit"},
            # {"name": "jira_assetsKey", "label": "Assets Epic Key", "type": "QLineEdit"},
            # {"name": "jira_shotsKey", "label": "Shots Epic Key", "type": "QLineEdit"},
            {"name": "jira_cutInID", "label": "Cut In Field ID", "type": "QLineEdit"},
            {"name": "jira_cutOutID", "label": "Cut Out Field ID", "type": "QLineEdit"},
            {"name": "jira_showTaskStatus", "label": "Show Task Status", "type": "QCheckBox", "default": True},
            # {"name": "jira_showProductStatus", "label": "Show Product Status", "type": "QCheckBox", "default": True},
            # {"name": "jira_showMediaStatus", "label": "Show Media Status", "type": "QCheckBox", "default": True},
            {"name": "jira_allowNonExistentTaskPublishes", "label": "Allow publishes from non-existent tasks", "type": "QCheckBox", "default": True},
            {"name": "jira_allowLocalTasks", "label": "Allow local tasks", "type": "QCheckBox", "default": False},
            # {"name": "jira_allowAssetCreation", "label": "Allow asset creation", "type": "QCheckBox", "default": False},
            # {"name": "jira_allowShotCreation", "label": "Allow shot creation", "type": "QCheckBox", "default": False},
            # {"name": "jira_includePathInComments", "label": "Include full filepath in Kitsu comments", "type": "QCheckBox", "default": True},
            {"name": "jira_useUsername", "label": "Use Jira usernames", "type": "QCheckBox", "default": True},
            # {"name": "jira_syncPlaylists", "label": "Sync playlists", "type": "QCheckBox", "default": False},
            # {"name": "jira_syncDepartments", "label": "Auto Sync Departments", "type": "QCheckBox", "default": False},
            # {"name": "jira_syncDepsNow", "label": "Sync Kitsu settings", "tooltip": "Queries the existing departments in Kitsu and creates the same departments in the Prism project. Also syncs the \"Is Feedback Request\" status.", "type": "QPushButton", "callback": self.syncSettings},
            {"name": "jira_syncEntityConnections", "label": "Auto Sync Asset-Shot connections", "type": "QCheckBox", "default": False},
            # {"name": "jira_createFolders", "label": "Create Asset-/Shot-Folders", "tooltip": "Creates folders in your project folder for all Kitsu assets/shots/tasks.", "type": "QPushButton", "callback": self.prjMng.createLocalFolders},
        ]
        return data

    @err_catcher(name=__name__)
    def requestAttachments(self, issues, auth=None, allowCache=True):
        if auth is None:
            auth = self.prjMng.getAuthorization()

        if not isinstance(issues, list):
            issues = [issues]

        auth = self.prjMng.getAuthorization()
        username = auth.get("jira_username")
        apiToken = auth.get("jira_apiToken")
        base64_user_pass = base64.b64encode(f"{username}:{apiToken}".encode()).decode()
        headers = {"Authorization": f"Basic {base64_user_pass}", "Accept": "application/json"}
        attachments = {}

        def get_tasks(session):
            tasks = []
            for issue in issues:
                url = f"{self.getRemoteUrl()}/rest/api/3/issue/{issue}"
                tasks.append(session.get(url, headers=headers, ssl=False))
            return tasks

        async def get_attachments():
            async with aiohttp.ClientSession() as session:
                tasks = get_tasks(session)
                responses = await asyncio.gather(*tasks)
                issue_datas = []
                for response in responses:
                    stat = response.status
                    if stat == 429:
                        self.core.popup(f"Too many requests. [Status code {response.status}]")
                    elif stat != 200:
                        self.core.popup(f"An error has occured. [Status code {response.status}]")
                    issue_datas.append(await response.json())

                for i, issue in enumerate(issues):
                    attachments[issue] = []
                    if "fields" in issue_datas[i] and "attachment" in issue_datas[i]["fields"]: # If it has an img attachment
                        for attachment in issue_datas[i]["fields"]["attachment"]:
                            attachments[issue].append(attachment)

        asyncio.run(get_attachments())

        return attachments

    @err_catcher(name=__name__)
    def getThumbnail(self, entity):
        if entity.get('thumbnail_id', None) is None:
            return None

        temp_dir = tempfile.gettempdir()

        thumbnail_id = entity['thumbnail_id']
        base_name = os.path.basename(thumbnail_id)
        name, extension = os.path.splitext(base_name)

        thumbnail_path = os.path.join(temp_dir, 'prism_jira', name + extension)

        if os.path.exists(thumbnail_path) == False:
            thumbnail = self.makeDbRequest(self.JIRA, "attachment", thumbnail_id).get()
            os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
            with open(thumbnail_path, 'wb') as f:
                f.write(thumbnail)
                f.close()

        return self.core.media.getPixmapFromPath(thumbnail_path)

    @err_catcher(name=__name__)
    def getTaskStatusList(self):
        # TODO
        return []
        # prjId = self.getCurrentProjectKey()
        # if not prjId:
            # return []

        # statuses = []
        # kPrj = self.makeDbRequest("project", "get_project", prjId)
        # if not kPrj:
            # return []

        # kstatuses = kPrj["task_statuses"]
        # allKstatuses = self.makeDbRequest("task", "all_task_statuses") or []

        # try:
            # user = self.makeDbRequest("client", "get_current_user", quiet=True)
        # except Exception:
            # return []

        # for kstatus in allKstatuses:
            # if kstatus["id"] not in kstatuses and kstatus["name"] != "Todo":
                # continue

            # allowed = True
            # if user["role"] == "user" and not kstatus["is_artist_allowed"]:
                # allowed = False

            # if user["role"] == "client" and not kstatus["is_client_allowed"]:
                # allowed = False

            # if kstatus["name"] == "Todo":
                # allowed = False

            # statuses.append({
                # "name": kstatus["name"],
                # "abbreviation": kstatus["short_name"],
                # "color": [int(kstatus["color"].lstrip("#")[i:i+2], 16) for i in (0, 2, 4)],
                # "tasks": True,
                # "products": False,
                # "media": False,
                # "allowedToSet": allowed
            # })
        
        # return statuses

    @err_catcher(name=__name__)
    def getMediaStatusList(self):
        # TODO
        status = [
            {
                "name": "neutral",
                "abbreviation": "neutral",
                "color": [170, 170, 170],
                "tasks": False,
                "products": False,
                "media": True,
                "allowedToSet": True
            },
            {
                "name": "validated",
                "abbreviation": "validated",
                "color": [103, 190, 72],
                "tasks": False,
                "products": False,
                "media": True,
                "allowedToSet": True
            },
            {
                "name": "rejected",
                "abbreviation": "rejected",
                "color": [255, 56, 96],
                "tasks": False,
                "products": False,
                "media": True,
                "allowedToSet": True
            }
        ]
        return status

    @err_catcher(name=__name__)
    def getProductStatusList(self):
        # TODO
        status = [
            {
                "name": "neutral",
                "abbreviation": "neutral",
                "color": [170, 170, 170],
                "tasks": False,
                "products": True,
                "media": False,
                "allowedToSet": True
            },
            {
                "name": "validated",
                "abbreviation": "validated",
                "color": [103, 190, 72],
                "tasks": False,
                "products": True,
                "media": False,
                "allowedToSet": True
            },
            {
                "name": "rejected",
                "abbreviation": "rejected",
                "color": [255, 56, 96],
                "tasks": False,
                "products": True,
                "media": False,
                "allowedToSet": True
            }
        ]
        return status

    @err_catcher(name=__name__)
    def getRemoteUrl(self):
        url = self.core.getConfig("prjManagement", "jira_url", config="project") or ""
        while url.endswith("/"):
            url = url[:-1]

        return url

    @err_catcher(name=__name__)
    def getExampleUrl(self):
        return "https://example.atlassian.net/"

    @err_catcher(name=__name__)
    def getCurrentUrl(self):
        # TODO
        return self.makeDbRequest("client", "get_host", allowCache=False)

    def validateUrl(self, url):
        if not url:
            return False

        with self.core.waitPopup(self.core, "Connecting to %s. Please wait..." % url):
            url = url.strip("\\/")
            import requests
            try:
                response = requests.post(url)
            except:
                return False

        if response.status_code in [200, 400]:
            return True

        return False

    @err_catcher(name=__name__)
    def openInBrowser(self, entityType, entity):
        prjId = self.getCurrentProjectKey()
        if not prjId:
            return

        if entityType == "asset":
            entityId = self.getAssetId(entity, prjId)
            suffix = "/browse/%s" % (entityId)
        elif entityType == "shot":
            entityId = self.getShotId(entity, prjId)
            suffix = "/browse/%s" % (entityId)
        elif entityType == "task":
            taskId = entity.get("id", "")
            suffix = "/browse/%s" % (entity.get("type", "") + "s", taskId)
        # TODO - 
        # elif entityType in ["productVersion", "mediaVersion"]:
        #     etype = entity.get("type", "")
        #     commentId = entity.get("id", "")
        #     comment = self.makeDbRequest("task", "get_comment", commentId) or {}
        #     taskId = comment.get("object_id")

        #     if not taskId:
        #         if entityType == "productVersion":
        #             taskname = entity.get("product", "")
        #         elif entityType == "mediaVersion":
        #             taskname = entity.get("identifier", "")

        #         task = self.getTask(entity, None, taskname) or {}
        #         taskId = task.get("id", "")

        #     suffix = "%s/tasks/%s" % (etype + "s", taskId)
        # elif entityType == "playlist":
        #     playlistId = entity.get("id", "")
        #     suffix = "playlists/%s" % playlistId

        rmtSite = self.getRemoteUrl()
        url = rmtSite + suffix
        self.core.openWebsite(url)

    @err_catcher(name=__name__)
    def showTaskStatus(self):
        return self.core.getConfig("prjManagement", "jira_showTaskStatus", config="project", dft=True)

    @err_catcher(name=__name__)
    def showProductStatus(self):
        return self.core.getConfig("prjManagement", "jira_showProductStatus", config="project", dft=True)

    @err_catcher(name=__name__)
    def showMediaStatus(self):
        return self.core.getConfig("prjManagement", "jira_showMediaStatus", config="project", dft=True)

    @err_catcher(name=__name__)
    def getAllowNonExistentTaskPublishes(self):
        return self.core.getConfig("prjManagement", "jira_allowNonExistentTaskPublishes", config="project", dft=True)

    @err_catcher(name=__name__)
    def getAllowLocalTasks(self):
        return self.core.getConfig("prjManagement", "jira_allowLocalTasks", config="project", dft=False)

    @err_catcher(name=__name__)
    def getAllowAssetCreation(self):
        return self.core.getConfig("prjManagement", "jira_allowAssetCreation", config="project", dft=False)

    @err_catcher(name=__name__)
    def getAllowShotCreation(self):
        return self.core.getConfig("prjManagement", "jira_allowShotCreation", config="project", dft=False)

    @err_catcher(name=__name__)
    def getIncludePathInComments(self):
        return self.core.getConfig("prjManagement", "jira_includePathInComments", config="project", dft=True)

    @err_catcher(name=__name__)
    def getUseKitsuUsername(self):
        return self.core.getConfig("prjManagement", "jira_useUsername", config="project", dft=True)

    @err_catcher(name=__name__)
    def getSyncPlaylists(self):
        return self.core.getConfig("prjManagement", "jira_syncPlaylists", config="project", dft=False)

    @err_catcher(name=__name__)
    def getSyncDepartments(self):
        return self.core.getConfig("prjManagement", "jira_syncDepartments", config="project", dft=False)

    @err_catcher(name=__name__)
    def getSyncEntityConnections(self):
        return self.core.getConfig("prjManagement", "jira_syncEntityConnections", config="project", dft=False)

    @err_catcher(name=__name__)
    def makeJqlQuery(self, query, priority=False):
        cmpnts = tuple(self.getCurrentComponents().split(", "))
        cmpntsSubstring = f'component in {cmpnts}' if len(cmpnts)>1 else f'component = {cmpnts[0]}'

        jqlQuery = f'project = {self.getCurrentProjectKey()} AND {cmpntsSubstring} AND {query} ORDER BY {"priority DESC, " if priority else "" }summary ASC'

        return jqlQuery

    @err_catcher(name=__name__)
    def makeDbRequest(self, module, method, args=None, popup=None, allowCache=True, quiet=False):
        if not isinstance(args, list):
            if args:
                args = [args]
            else:
                args = []

        if module not in self.dbCache:
            self.dbCache[module] = {}

        if method not in self.dbCache[module]:
            self.dbCache[module][method] = []

        if allowCache:
            for request in self.dbCache[module][method]:
                if request["args"] == args:
                    return request["result"]
        else:
            self.dbCache[module][method] = [r for r in self.dbCache[module][method] if r["args"] != args]

        # if self.requestInProgress:
            # while self.requestInProgress:
                # time.sleep(0.1)

            # return self.makeDbRequest(method, args=args, popup=popup, allowCache=allowCache)

        #self.requestInProgress = True
        # self.core.popup("make request: %s, %s, %s" % (module, method, args))
        
        if popup and not popup.msg and getattr(self, "allowRequestPopups", True):
            popup.show()

        try:
            if module == requests:
                # self.core.popup(url)
                auth = self.prjMng.getAuthorization()
                username = auth.get("jira_username")
                apiToken = auth.get("jira_apiToken")
                base64_user_pass = base64.b64encode(f"{username}:{apiToken}".encode()).decode()
                kwargs = {"headers": {"Authorization": f"Basic {base64_user_pass}", "Accept": "application/json"},
                          "timeout": 5}
                result = getattr(module, method)(*args, **kwargs)
            else :
                result = getattr(module, method)(*args)
        except Exception as e:
            #self.requestInProgress = False
            # traceback.print_stack()
            logger.debug(method)
            logger.debug(args)
            msg = "Could not request Jira data:\n\n%s" % e
            if "was cancelled by the user" in msg:
                logger.debug(msg)
                self.allowLoginRequest = False
                self.logout()
            else:
                self.core.popup(msg)

            return

        data = {"args": args, "result": result}
        self.dbCache[module][method].append(data)
        # self.requestInProgress = False
        return result

    @err_catcher(name=__name__)
    def clearDbCache(self):
        self.dbCache = {}

    @err_catcher(name=__name__)
    def isLoggedIn(self):
        # TODO
        # add check to see if the atlassian site is up
        # if not self.makeDbRequest("client", "host_is_up", quiet=True):
        #     return False

        try:
            user = self.makeDbRequest(self.JIRA, "myself", quiet=True)
        except Exception:
            return False

        loggedIn = bool(user)
        return loggedIn

    @err_catcher(name=__name__)
    def login(self, auth=None, quiet=False):
        
        if auth is None:
            auth = self.prjMng.getAuthorization()
        
        url = auth.get("url")
        username = auth.get("jira_username")
        apiToken = auth.get("jira_apiToken")
        self.core.users.setUserReadOnly(False)

        if not url:
            if not quiet and self.allowUrlRequest:
                self.allowUrlRequest = False
                msg = "No Jira Url is set in the project settings. Cannot connect to Kitsu."
                result = self.core.popupQuestion(msg, buttons=["Setup Kitsu...", "Close"], icon=QMessageBox.Warning)
                if result == "Setup Kitsu...":
                    self.prjMng.openSetupDlg()

            return

        if not username or not apiToken:
            if not quiet:
                if self.core.uiAvailable and self.allowLoginRequest:
                    self.prjMng.requestLogin(url)

                return

            if not username:
                msg = "No Jira username is set in the user settings. Cannot login into Jira."
                logger.warning(msg)
                return

            if not apiToken:
                msg = "No Jira API Key is set in the user settings. Cannot login into Jira."
                logger.warning(msg)
                return

        url = url.strip("\\/")
        failed = False
        # try:
        #     self.gazu.client.set_host(url)
        # except Exception as e:
        #     logger.warning(e)
        #     failed = True

        # if failed or not self.makeDbRequest("client", "host_is_up", allowCache=False):
        #     msg = "Failed to connect to Kitsu.\n\nUrl: %s" % url
        #     if not quiet:
        #         self.core.popup(msg)
        # 
        #     return
        self.JIRA = self.jiraApi.JIRA(server=url, basic_auth=(username, apiToken))
        logger.debug("logged in into Jira")

        # try:
        #     self.jiraApi.JIRA(server=url, basic_auth=(username, apiToken))
        #     logger.debug("logged in into Jira")
        # except Exception:
        #     self.clearDbCache()
        #     logger.warning("login failed")
        # else:
        #     self.allowLoginRequest = True
        #     self.allowUrlRequest = True
        #     self.clearDbCache()
        #     if self.getUseKitsuUsername():
        #         self.prjMng.setLocalUsername()

    @err_catcher(name=__name__)
    def logout(self):
        # self.JIRA.
        self.clearDbCache()

    @err_catcher(name=__name__)
    def getUsername(self):
        text = "Querying username - please wait..."
        popup = self.core.waitPopup(self.core, text, hidden=True)
        with popup:
            user = self.makeDbRequest(self.JIRA, "myself", popup=popup)

        if not user:
            return

        username = user["displayName"]
        return username

    @err_catcher(name=__name__)
    def getProjects(self, parent=None):
        text = "Querying projects - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            if not self.prjMng.ensureLoggedIn():
                return

            pstatus = self.makeDbRequest("project", "all_project_status", allowCache=False)
            kprojects = self.makeDbRequest("project", "all_open_projects", allowCache=False) or []
            projects = []
            for project in kprojects:
                pdata = {
                    "name": project["name"],
                    "status": [s for s in pstatus if s["id"] == project["project_status_id"]][0],
                    "start_date": project["start_date"] or "",
                    "end_date": project["end_date"] or "",
                    "id": project["id"],
                }
                projects.append(pdata)

            return projects

    @err_catcher(name=__name__)
    def getCurrentComponents(self):
        if not self.prjMng.ensureLoggedIn(quiet=True):
            return

        components = self.core.getConfig("prjManagement", "jira_components", config="project")
        if not components:
            msg = "No Jira component/components specified in the project settings."
            self.core.popup(msg)
            return
            
        return components

    @err_catcher(name=__name__)
    def syncSettings(self, window=None):
        self.prjMng.syncDepartments(window)
        self.syncFeedbackStatus()

    @err_catcher(name=__name__)
    def getFeedbackStatus(self, allowCache=True):
        path = "/data/task-status"
        statuses = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default], allowCache=allowCache) or {}
        fb = []
        for status in statuses:
            if status.get("is_feedback_request"):
                fb.append(status["short_name"])

        if "wfa" in fb:
            return "wfa"
        elif fb:
            return fb[0]
        else:
            return "wfa"

    @err_catcher(name=__name__)
    def syncFeedbackStatus(self, allowCache=True):
        fb = self.getFeedbackStatus(allowCache=allowCache)
        self.core.setConfig("prjManagement", "kitsu_versionPubStatus", val=fb, config="project")

    @err_catcher(name=__name__)
    def getConnectedEntities(self, entity):
        centities = []
        if not entity:
            return centities

        prjKey = self.getCurrentProjectKey()
        if prjKey is None:
            return

        if entity.get("type") == "asset":
            entityId = self.getAssetId(entity)
            rmtShots = self.getShots()
            for rmtShot in rmtShots:
                cassets = self.getConnectedEntities(rmtShot)
                if entityId in [asset["id"] for asset in cassets]:
                    centities.append(rmtShot)

        elif entity.get("type") == "shot":
            if not entity.get("sequence") or entity.get("shot") == "_sequence":
                return centities

            entityId = self.getShotId(entity)
            rmtEntities = self.makeDbRequest("asset", "all_assets_for_shot", [{"id": entityId}])
            for rmtEntity in rmtEntities:
                rmtAssets = self.getAssets()
                for rmtAsset in rmtAssets:
                    if rmtAsset["id"] == rmtEntity["id"]:
                        centities.append(rmtAsset)

        return centities

    @err_catcher(name=__name__)
    def setConnectedEntities(self, entities, connectedEntities, add=False, remove=False, setReverse=True):
        msg = "Entity connection cannot get set in Prism, if your project in connected to Kitsu.\n\nPlease set the entity connection in Kitsu."
        result = self.core.popupQuestion(msg, buttons=["Open Kitsu", "Close"], icon=QMessageBox.Warning)
        if result == "Open Kitsu":
            self.openInBrowser(entities[0]["type"], entities[0])

        return False

    @err_catcher(name=__name__)
    def getCurrentProjectKey(self):
        prjKey = self.core.getConfig("prjManagement", "jira_projectKey", config="project")
        
        if not prjKey:
            msg = "No Jira project key is specified in the project settings."
            logger.warning(msg)
            return None

        return prjKey

    @err_catcher(name=__name__)
    def getAssetDepartments(self, allowCache=True):
        departments = {}
        prjKey = self.getCurrentProjectKey()
        path = "data/projects/%s" % prjKey
        prj = self.makeDbRequest("project", "raw.get", [path, self.gazu.task.default], allowCache=allowCache)
        if not prj:
            return departments

        kTaskTypeIds = prj["task_types"]
        kTaskTypes = []
        if kTaskTypeIds:
            for kTaskTypeId in kTaskTypeIds:
                path = "/data/task-types/%s" % kTaskTypeId
                ttype = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default], allowCache=allowCache)
                kTaskTypes.append(ttype)
        else:
            path = "/data/task-types"
            kTaskTypes = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default], allowCache=allowCache)

        for kTaskType in sorted(kTaskTypes, key=lambda x: x["priority"]):
            if kTaskType["for_entity"] != "Asset":
                continue

            typedep = self.getDepartmentNameByTaskTypeId(kTaskType["id"])
            if typedep not in departments:
                departments[typedep] = []

            departments[typedep].append(kTaskType["name"])

        departments = [{"name": d, "abbreviation": d, "defaultTasks": departments[d]} for d in departments]
        return departments

    @err_catcher(name=__name__)
    def getShotDepartments(self, allowCache=True):
        departments = {}
        prjKey = self.getCurrentProjectKey()
        path = "data/projects/%s" % prjKey
        prj = self.makeDbRequest("project", "raw.get", [path, self.gazu.task.default], allowCache=allowCache)
        if not prj:
            return departments

        kTaskTypeIds = prj["task_types"]
        kTaskTypes = []
        if kTaskTypeIds:
            for kTaskTypeId in kTaskTypeIds:
                path = "/data/task-types/%s" % kTaskTypeId
                ttype = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default], allowCache=allowCache)
                kTaskTypes.append(ttype)
        else:
            path = "/data/task-types"
            kTaskTypes = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default], allowCache=allowCache)

        for kTaskType in sorted(kTaskTypes, key=lambda x: x["priority"]):
            if kTaskType["for_entity"] != "Shot":
                continue

            typedep = self.getDepartmentNameByTaskTypeId(kTaskType["id"])
            if typedep not in departments:
                departments[typedep] = []

            departments[typedep].append(kTaskType["name"])

        departments = [{"name": d, "abbreviation": d, "defaultTasks": departments[d]} for d in departments]
        return departments

    @err_catcher(name=__name__)
    def getAssetFolders(self, path=None, parent=None):
        assetFolders = []
        if path:
            return assetFolders

        assets = self.getAssets(path=path) or []
        for asset in assets:
            cat = os.path.dirname(asset["asset_path"])
            if cat and cat not in assetFolders:
                assetFolders.append(cat)

        return assetFolders

    @err_catcher(name=__name__)
    def getAssets(self, path=None, parent=None, allowCache=True):
        text = "Querying assets - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            jiraAssets = self.makeDbRequest(self.JIRA, "search_issues", [self.makeJqlQuery(f'type = Asset'), 0, 0], popup=popup, allowCache=allowCache)
            
            assets = []
            for jiraAsset in jiraAssets:
                assetName = re.split(r"\s*-\s*", jiraAsset.get_field("summary"))[-1].replace(" ", "_")
                
                assetCategory = re.split(r"\s*-\s*", jiraAsset.get_field("parent").get_field("summary"))[-1].replace(" ", "_")
                description = jiraAsset.get_field("description").split('\n')[0] if jiraAsset.get_field("description") else "No Description"
                
                assetPath = f"{assetCategory}/{assetName}"
                
                assetData = {
                    "asset_path": assetPath,
                    "type": "asset",
                    "description": description,
                    "thumbnail_id": None,
                    "id": jiraAsset.key,
                }
                assets.append(assetData)

            allAttachments = self.makeDbRequest(self, "requestAttachments", [[asset.get("id") for asset in assets]])
            
            for i, asset in enumerate(assets) :
                issueKey = asset.get("id")
                # for issueAttachments in allAttachments[issueKey]: # Find most recent attachment eventually
                assets[i]["thumbnail_id"] = allAttachments[issueKey][0].get("id") if len(allAttachments[issueKey]) != 0 else None
                
            return assets

    @err_catcher(name=__name__)
    def getAssetId(self, entity, prjId=None, popup=None):
        if not prjId:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return
        assetPath = entity.get("asset_path", "").replace("\\", "/").split("/")[-1].replace("_", " ")
        asset = [x for x in self.makeDbRequest(self.JIRA, "search_issues", [self.makeJqlQuery(f'type = Asset'), 0, 0], popup=popup, allowCache=True) if assetPath in x.get_field("summary")]

        if asset:
            return asset[0].key

    @err_catcher(name=__name__)
    def getEpisodesEnabled(self):
        text = "Checking episodes enabled - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=None, hidden=True)
        with popup:
            prjId = self.getCurrentProjectId()
            if prjId is None:
                return

            filters = [
                ["project", "is", {"type": "Project", "id": prjId}],
            ]
            rmtEps = self.makeDbRequest("find", ["Episode", filters, []], popup=popup) or {}

        return bool(rmtEps)

    @err_catcher(name=__name__)
    def getEpisodes(self, parent=None, allowCache=True):
        shots = self.getShots(parent=parent, allowCache=allowCache)
        episodes = []
        for shot in shots:
            if "episode" not in shot:
                continue

            if shot["episode"] in [episode["episode"] for episode in episodes]:
                continue

            epData = {
                "type": "shot",
                "episode": shot["episode"],
                "sequence": "_episode",
                "shot": "_sequence",
            }
            episodes.append(epData)

        return episodes

    @err_catcher(name=__name__)
    def getSequences(self, parent=None, allowCache=True, episode=None):
        shots = self.getShots(parent=parent, allowCache=allowCache, episode=episode)
        sequences = []
        for shot in shots:
            if "sequence" not in shot:
                continue

            if shot["sequence"] in [sequence["sequence"] for sequence in sequences if not episode or episode == sequence.get("episode")]:
                continue

            seqData = {
                "type": "shot",
                "sequence": shot["sequence"],
                "shot": "_sequence",
            }
            if episode:
                seqData["episode"] = episode

            sequences.append(seqData)

        return sequences

    @err_catcher(name=__name__)
    def getShots(self, parent=None, allowCache=True, episode=None, sequence=None):
        text = "Querying shots - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            useEpisodes = self.core.getConfig(
                "globals",
                "useEpisodes",
                config="project",
            ) or False

            jiraSequences = [x for x in self.makeDbRequest(self.JIRA, "search_issues", [self.makeJqlQuery(f'type = "Shot"'), 0, 0], popup=popup, allowCache=allowCache) if "sequence" in x.get_field("summary")]
            shots = []

            for jiraSequence in jiraSequences:
                jiraShots = [x for x in self.makeDbRequest(self.JIRA, "search_issues", [self.makeJqlQuery(f'type = "Shot"'), 0, 0], popup=popup, allowCache=allowCache) if jiraSequence.get_field("summary").split("_sequence")[0] in x.get_field("summary")]
                
                for jiraShot in jiraShots:
                    cutInID = self.core.getConfig("prjManagement", "jira_cutInID", config="project")
                    cutOutID = self.core.getConfig("prjManagement", "jira_cutOutID", config="project")
                    cutIn = jiraShot.get_field(f"customfield_{cutInID}")
                    cutOut = jiraShot.get_field(f"customfield_{cutOutID}")
                    if cutIn:
                        cutIn = int(cutIn)
                    if cutOut:
                        cutOut = int(cutOut)
                        
                    seqName = seqName = "_".join(re.split(r"\s*-\s*", jiraSequence.get_field("summary").split("_sequence")[0])[1:])
                    if not useEpisodes:
                        seqName = "_".join(re.split(r"\s*-\s*", jiraSequence.get_field("summary").split("_sequence")[0]))
                    
                    data = {
                        "type": "shot",
                        "shot": "_".join(re.split(r"\s*-\s*", jiraShot.get_field("summary"))[1:]),
                        "sequence": seqName,
                        "id": jiraShot.key,
                        "start": cutIn,
                        "end": cutOut,
                        # "thumbnail_url": self.getThumbnail(jiraShot.key),
                    }

                    if useEpisodes:
                        data["episode"] = re.split(r"\s*-\s*", jiraSequence.get_field("summary").split("_sequence")[0])[0]

                    if episode and episode != data.get("episode"):
                        continue

                    if sequence and sequence != data.get("sequence"):
                        continue

                    shots.append(data)

            return shots

    @err_catcher(name=__name__)
    def getShotByEntity(self, entity):
        shots = self.getShots()
        if shots is None:
            return

        useEpisodes = self.core.getConfig(
            "globals",
            "useEpisodes",
            config="project",
        ) or False
        for shot in shots:
            if useEpisodes:
                if shot["episode"] == entity["episode"] and shot["sequence"] == entity["sequence"] and shot["shot"] == entity["shot"]:
                    return shot
            else:
                if shot["sequence"] == entity["sequence"] and shot["shot"] == entity["shot"]:
                    return shot

        # msg = 'Could not find shot "%s" in Kitsu.' % self.core.entities.getShotName(entity)
        # self.core.popup(msg)
        return

    @err_catcher(name=__name__)
    def getShotId(self, entity, prjId=None):
        if not prjId:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

        shot = self.getShotByEntity(entity)
        if shot:
            return shot.get("id")

    # @err_catcher(name=__name__)
    # def getThumbnail(self, entity):
    #     tmpFile = tempfile.NamedTemporaryFile(suffix=".png")
    #     imgPath = tmpFile.name
    #     tmpFile.close()
    #     # if "start_date" in entity:
    #     #     self.makeDbRequest("files", "download_project_avatar", [entity["id"], imgPath], allowCache=False)
    #     # elif "thumbnail_id" in entity:
    #     #     if not entity["thumbnail_id"]:
    #     #         return
    #     self.requestAttachments(entity["id"])
    #     #self.makeDbRequest("files", "download_preview_file_thumbnail", [entity["thumbnail_id"], imgPath], allowCache=False)

    #     pixmap = self.core.media.getPixmapFromPath(imgPath)
    #     try:
    #         os.remove(imgPath)
    #     except Exception:
    #         pass

    #     return pixmap

    # @err_catcher(name=__name__)
    # def getThumbnail(self, entity):
    #     # if entity.get('thumbnail', None) is None:
    #     #     return None

    #     temp_dir = tempfile.gettempdir()

    #     issue = self.makeDbRequest("issue", [entity["id"]])
    #     base_name = os.path.basename(thumbnail_url)
    #     name, extension = os.path.splitext(base_name)

    #     thumbnail_path = os.path.join(temp_dir, 'prism_jira', name + extension)

    #     if os.path.exists(thumbnail_path) == False:
    #         thumbnail = self.aq.do_request('GET', entity["thumbnail"], decoding=False)

    #         os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
    #         with open(thumbnail_path, 'wb') as f:
    #             f.write(thumbnail.content)
    #             f.close()

    #     return self.core.media.getPixmapFromPath(thumbnail_path)

    @err_catcher(name=__name__)
    def getTask(self, entity, dep, task, parent=None):
        tasks = self.getTasksFromEntity(entity) or []
        for t in tasks:
            if dep and t["department"] != dep:
                continue

            if t["task"] != task:
                continue

            return t

    @err_catcher(name=__name__)
    def getTasksFromEntity(self, entity, parent=None, allowCache=True):
        text = "Querying tasks - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)

        if (allowCache == False):
            self.clearDbCache()
            self.getAssets()
            self.getShots()

        with popup:
            tasks = []
            if (entity["type"] == 'asset'):
                assetId = self.getAssetId(entity, popup=popup)

                entityLinks = [x for x in self.makeDbRequest(self.JIRA, "issue", [assetId]).fields.issuelinks if x.type.name=="Entity Link"]
                
                entityTasks = []
                for i, x in enumerate(entityLinks):
                    entityTasks.append(self.makeDbRequest(self.JIRA, "issue", [entityLinks[i].inwardIssue.key]))

                if len(entityTasks) > 0:
                    for entityTask in entityTasks:
                        dep = entityTask.get_field("labels")[0] if len(entityTask.get_field("labels")) > 0 else None
                        taskName = re.split(r"\s*-\s*", entityTask.get_field("summary"))[-1]
                        if dep:
                            data = {
                                "department": dep,
                                "task": taskName,
                                "status": str(entityTask.get_field("status")),
                                "id": entityTask.key,
                            }
                            tasks.append(data)
            elif (entity["type"] == 'shot'):
                shotId = self.getShotId(entity)
                if not shotId:
                    return

                entityLinks = [x for x in self.makeDbRequest(self.JIRA, "issue", [shotId]).fields.issuelinks if x.type.name=="Entity Link"]
                
                entityTasks = []
                for i, x in enumerate(entityLinks):
                    entityTasks.append(self.makeDbRequest(self.JIRA, "issue", [entityLinks[i].inwardIssue.key]))

                if len(entityTasks) > 0:
                    for entityTask in entityTasks:
                        dep = entityTask.get_field("labels")[0] if len(entityTask.get_field("labels")) > 0 else None
                        taskName = re.split(r"\s*-\s*", entityTask.get_field("summary"))[-1]
                        if dep:
                            data = {
                                "department": dep,
                                "task": taskName,
                                "status": str(entityTask.get_field("status")),
                                "id": entityTask.key,
                            }
                            tasks.append(data)
            return tasks

    @err_catcher(name=__name__)
    def getTaskId(self, entity, prjId, taskname):
        task = self.getTask(entity, None, taskname)
        if task:
            return task.get("id")

    @err_catcher(name=__name__)
    def getTaskTypeNameByTaskTypeId(self, tid):
        path = "/data/task-types/%s" % tid
        ttype = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default])
        return ttype["name"]

    @err_catcher(name=__name__)
    def getDepartmentNameByTaskTypeId(self, tid):
        path = "/data/task-types/%s" % tid
        ttype = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default])
        if not ttype:
            return

        depId = ttype["department_id"]
        if depId is None:
            return

        path = "/data/departments/%s" % ttype["department_id"]
        dep = self.makeDbRequest("task", "raw.get", [path, self.gazu.task.default])
        return dep["name"]

    @err_catcher(name=__name__)
    def setTaskStatus(self, entity, department, task, status, parent=None):
        text = "Setting status - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            tasks = self.getTasksFromEntity(entity, parent=parent)
            for tsk in tasks:
                if tsk["department"] != department:
                    continue

                if tsk["task"] == task:
                    taskId = tsk["id"]
                    break
            else:
                msg = "Couldn't find matching task in Kitsu. Failed to set status."
                self.core.popup(msg)
                return False

            comment = "Setting task status to: %s" % status
            statusDict = self.makeDbRequest("task", "get_task_status_by_short_name", status, popup=popup)
            if not statusDict:
                msg = "The status \"%s\" doesn't exist in Kitsu. Cannot set status." % status
                self.core.popup(msg)
                return

            result = self.makeDbRequest("task", "add_comment", [taskId, statusDict, comment], allowCache=False)
            if result and result.get("id"):
                self.makeDbRequest("task", "all_comments_for_task", taskId, allowCache=False)
                self.getTasksFromEntity(entity, parent=parent, allowCache=False)
                self.getAssignedTasks(allowCache=False)
                return True

    @err_catcher(name=__name__)
    def getProductVersions(self, entity, parent=None, allowCache=True):
        text = "Querying versions - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            ktasks = self.getTasksFromEntity(entity, allowCache=allowCache)
            versionData = []
            for ktask in ktasks:
                comments = self.makeDbRequest("task", "all_comments_for_task", ktask, allowCache=allowCache) or []
                for comment in comments:
                    vdata = self.getVersionFromNoteText(comment["text"], entity=entity)
                    if not vdata or len(vdata) < 3:
                        continue

                    if comment["previews"]:
                        status = comment["previews"][0]["validation_status"]
                    else:
                        status = "unknown"

                    product = vdata["task"]
                    versionNumber = vdata["version"]
                    data = {
                        "product": product,
                        "version": versionNumber,
                        "status": status,
                        "id": comment["id"],
                        "taskId": ktask["id"]
                    }
                    versionData.append(data)

            return versionData

    @err_catcher(name=__name__)
    def getProductVersion(self, entity, product, versionName):
        versions = self.getProductVersions(entity) or []
        for version in versions:
            if version.get("product") != product:
                continue

            if version["version"] == versionName:
                return version

    @err_catcher(name=__name__)
    def getProductVersionStatus(self, entity, product, versionName):
        version = self.getProductVersion(entity, product, versionName)
        if version:
            return version["status"]

    @err_catcher(name=__name__)
    def setProductVersionStatus(self, entity, product, versionName, status, parent=None):
        text = "Setting status - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            versions = self.getProductVersions(entity, parent=parent)
            for version in versions:
                if version["product"] != product:
                    continue

                if version["version"] == versionName:
                    versionId = version["id"]
                    taskId = version["taskId"]
                    break
            else:
                msg = "Couldn't find matching task in Kitsu. Failed to set status."
                self.core.popup(msg)
                return False

            comments = self.makeDbRequest("task", "all_comments_for_task", taskId)
            for comment in comments:
                if comment["id"] == versionId:
                    commentData = comment
                    break
            else:
                msg = "Couldn't find comment for this version in Kitsu."
                self.core.popup(msg)
                return

            if not commentData["previews"]:
                msg = "The comment doesn't have a revision attached in Kitsu. Cannot set status."
                self.core.popup(msg)
                return

            result = self.makeDbRequest("task", "raw.update", ["preview-files", commentData["previews"][0]["id"], {"validation_status": status}], allowCache=False, quiet=True)
            if result and result.get("id"):
                self.getProductVersions(entity, parent=parent, allowCache=False)
                return True
            else:
                msg = "Couldn't set status. Make sure you have the required permissions in Kitsu."
                self.core.popup(msg)
                return

    @err_catcher(name=__name__)
    def publishProduct(self, path, entity, task, version, description="", preview=None, parent=None, origTask=None):
        # TODO: Publish as attachment to original issue? Do we *need* to publish the file? Can we just make a new issue ready for QA by supes?
        text = "Publishing product. Please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            if entity.get("type") == "asset":
                entityName = entity.get("asset_path", "").replace("\\", "/")
                kasset = self.getAssetId(entity)
                if not kasset:
                    msg = "The asset \"%s\" doesn't exist in Kitsu. Publish canceled." % entityName
                    self.core.popup(msg)
                    return

                ktasks = self.makeDbRequest("task", "all_tasks_for_asset", kasset)

            elif entity.get("type") == "shot":
                kshot = self.getShotByEntity(entity)
                if not kshot:
                    msg = "The shot \"%s\" doesn't exist in Kitsu. Publish canceled." % self.core.entities.getShotName(entity)
                    self.core.popup(msg)
                    return

                ktasks = self.makeDbRequest("task", "all_tasks_for_shot", kshot)
                entityName = self.core.entities.getShotName(entity)
            else:
                msg = "Invalid entity."
                self.core.popup(msg)
                return

            for ktask in ktasks:
                if ktask["task_type_name"] != task:
                    continue

                break
            else:
                if self.getAllowNonExistentTaskPublishes():
                    self.prjMng.showPublishNonExistentTaskDlg(path, entity, task, version, description=description, preview=preview, parent=parent, mode="product")
                    return
                else:
                    msg = "The task \"%s\" doesn't exist in Kitsu. Publish canceled." % task
                    self.core.popup(msg)
                    return

            tasklabel = origTask or task
            versionName = "%s_%s_%s" % (
                entityName,
                tasklabel,
                version,
            )
            if self.getIncludePathInComments():
                comment = "%s:\n\n%s" % (versionName, path)
            else:
                comment = "%s:\n\n%s" % (versionName, os.path.basename(path))

            statusCode = self.core.getConfig("prjManagement", "kitsu_versionPubStatus", config="project")
            status = self.makeDbRequest("task", "get_task_status_by_short_name", statusCode)
            if not status:
                statusname = ktask.get("task_status_name", "")
                statusData = [s for s in self.prjMng.getTaskStatusList() if s["name"].lower() == statusname.lower()]
                if statusData:
                    taskStatusCode = statusData[0]["abbreviation"]
                    status = self.makeDbRequest("task", "get_task_status_by_short_name", taskStatusCode)
                else:
                    status = ""

                if not status:
                    msg = "The status \"%s\" doesn't exist in Kitsu. Publish canceled." % statusCode
                    self.core.popup(msg)
                    return

            logger.debug("publishing version to Kitsu: %s" % comment)
            try:
                createdComment = self.makeDbRequest("task", "add_comment", [ktask, status, comment], allowCache=False)
            except Exception:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                erStr = "ERROR:\n%s" % traceback.format_exc()
                self.core.popup(erStr, title="Kitsu Publish")
                return

            previewPath = self.core.getTempFilepath(filename="kitsuProduct.jpg")
            if preview:
                pixmap = preview
            else:
                pixmap = QPixmap(10, 10)
                pixmap.fill(Qt.black)

            self.core.media.savePixmap(pixmap, previewPath)
            if previewPath:
                popup.msg.setText("Uploading preview. Please wait...")
                QApplication.processEvents()
                self.gazu.task.add_preview(ktask, createdComment, previewPath)
                try:
                    os.remove(previewPath)
                except Exception:
                    pass

            if createdComment and createdComment.get("id"):
                self.getProductVersions(entity, parent=parent, allowCache=False)

            url = self.makeDbRequest("task", "get_task_url", ktask)
            data = {"url": url, "versionName": versionName}
            return data

    @err_catcher(name=__name__)
    def getMediaVersions(self, entity, parent=None, allowCache=True):
        # TODO : Figure out how to publish media and review media with Jira
        text = "Querying versions - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            ktasks = self.getTasksFromEntity(entity, allowCache=allowCache)
            versionData = []
            for ktask in ktasks:
                comments = self.makeDbRequest("task", "all_comments_for_task", ktask, allowCache=allowCache) or []
                for comment in comments:
                    vdata = self.getVersionFromNoteText(comment["text"], entity=entity)
                    if not vdata or len(vdata) < 3:
                        continue

                    if comment["previews"]:
                        status = comment["previews"][0]["validation_status"]
                    else:
                        status = "unknown"

                    identifier = vdata["task"]
                    versionNumber = vdata["version"]
                    data = {
                        "identifier": identifier,
                        "version": versionNumber,
                        "status": status,
                        "id": comment["id"],
                        "taskId": ktask["id"],
                        "kitsuTaskName": ktask["task"],
                    }
                    versionData.append(data)

            return versionData

    @err_catcher(name=__name__)
    def getMediaVersion(self, entity, identifierData, versionName):
        # TODO : Figure out how to publish media and review media with Jira
        versions = self.getMediaVersions(entity) or []
        for version in versions:
            if version.get("identifier") != identifierData["identifier"]:
                continue

            if version["version"] == versionName:
                return version

    @err_catcher(name=__name__)
    def getMediaVersionStatus(self, entity, identifierData, versionName):
        # TODO : Figure out how to publish media and review media with Jira
        version = self.getMediaVersion(entity, identifierData, versionName)
        if version:
            return version["status"]

    @err_catcher(name=__name__)
    def setMediaVersionStatus(self, entity, identifierData, versionName, status, parent=None):
        # TODO : Figure out how to publish media and review media with Jira
        text = "Setting status - please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent, hidden=True)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            versions = self.getMediaVersions(entity, parent=parent)
            for version in versions:
                if version["identifier"] != identifierData["identifier"]:
                    continue

                if version["version"] == versionName:
                    versionId = version["id"]
                    taskId = version["taskId"]
                    break
            else:
                msg = "Couldn't find matching task in Kitsu. Failed to set status."
                self.core.popup(msg)
                return False

            comments = self.makeDbRequest("task", "all_comments_for_task", taskId)
            for comment in comments:
                if comment["id"] == versionId:
                    commentData = comment
                    break
            else:
                msg = "Couldn't find comment for this version in Kitsu."
                self.core.popup(msg)
                return

            if not commentData["previews"]:
                msg = "The comment doesn't have a revision attached in Kitsu. Cannot set status."
                self.core.popup(msg)
                return

            result = self.makeDbRequest("task", "raw.update", ["preview-files", commentData["previews"][0]["id"], {"validation_status": status}], allowCache=False, quiet=True)
            if result and result.get("id"):
                self.getMediaVersions(entity, parent=parent, allowCache=False)
                return True
            else:
                msg = "Couldn't set status. Make sure you have the required permissions in Kitsu."
                self.core.popup(msg)
                return

    @err_catcher(name=__name__)
    def publishMedia(self, paths, entity, task, version, description="", uploadPreview=True, parent=None, origTask=None):
        # TODO : Figure out how to publish media and review media with Jira
        text = "Publishing media. Please wait..."
        popup = self.core.waitPopup(self.core, text, parent=parent)
        with popup:
            prjId = self.getCurrentProjectKey()
            if prjId is None:
                return

            if entity.get("type") == "asset":
                entityName = entity.get("asset_path", "").replace("\\", "/")
                kasset = self.getAssetId(entity)
                if not kasset:
                    msg = "The asset \"%s\" doesn't exist in Kitsu. Publish canceled." % entityName
                    self.core.popup(msg)
                    return

                ktasks = self.makeDbRequest("task", "all_tasks_for_asset", kasset)

            elif entity.get("type") == "shot":
                kshot = self.getShotByEntity(entity)
                if not kshot:
                    msg = "The shot \"%s\" doesn't exist in Kitsu. Publish canceled." % self.core.entities.getShotName(entity)
                    self.core.popup(msg)
                    return

                ktasks = self.makeDbRequest("task", "all_tasks_for_shot", kshot)
                entityName = self.core.entities.getShotName(entity)
            else:
                msg = "Invalid entity."
                self.core.popup(msg)
                return

            for ktask in ktasks:
                if ktask["task_type_name"] != task:
                    continue

                break
            else:
                if self.getAllowNonExistentTaskPublishes():
                    self.prjMng.showPublishNonExistentTaskDlg(paths, entity, task, version, description=description, uploadPreview=uploadPreview, parent=parent, mode="media")
                    return
                else:
                    msg = "The task \"%s\" doesn't exist in Kitsu. Publish canceled." % task
                    self.core.popup(msg)
                    return

            tasklabel = origTask or task
            versionName = "%s_%s_%s" % (
                entityName,
                tasklabel,
                version,
            )
            comment = versionName + ":\n\n"
            if self.getIncludePathInComments():
                comment += paths[0]
            else:
                comment += os.path.basename(paths[0])

            if description:
                comment += "\n\nDescription: %s" % description

            statusCode = self.core.getConfig("prjManagement", "kitsu_versionPubStatus", config="project")
            status = self.makeDbRequest("task", "get_task_status_by_short_name", statusCode)
            if not status:
                statusname = ktask.get("task_status_name", "")
                statusData = [s for s in self.prjMng.getTaskStatusList() if s["name"].lower() == statusname.lower()]
                if statusData:
                    taskStatusCode = statusData[0]["abbreviation"]
                    status = self.makeDbRequest("task", "get_task_status_by_short_name", taskStatusCode)
                else:
                    status = ""

                if not status:
                    msg = "The status \"%s\" doesn't exist in Kitsu. Publish canceled." % statusCode
                    self.core.popup(msg)
                    return

            logger.debug("publishing version to Kitsu: %s" % comment)
            try:
                createdComment = self.makeDbRequest("task", "add_comment", [ktask, status, comment], allowCache=False)
            except Exception:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                erStr = "ERROR:\n%s" % traceback.format_exc()
                self.core.popup(erStr, title="Kitsu Publish")
                return

            cleanupPreview = True
            if uploadPreview:
                if len(paths) == 1 and os.path.splitext(paths[0])[1] in [".mp4", ".jpg", ".png"]:
                    previewPath = paths[0]
                    cleanupPreview = False
                else:
                    previewPath = self.prjMng.createUploadableMedia(paths, popup=popup)

            else:
                previewPath = self.core.getTempFilepath(filename="kitsuMedia.jpg")
                pixmap = QPixmap(10, 10)
                pixmap.fill(Qt.black)
                self.core.media.savePixmap(pixmap, previewPath)
                previewPath = self.prjMng.createUploadableMedia([previewPath], popup=popup)

            if previewPath:
                popup.msg.setText("Uploading media. Please wait...")
                QApplication.processEvents()
                self.gazu.task.add_preview(ktask, createdComment, previewPath)
                if cleanupPreview:
                    try:
                        os.remove(previewPath)
                    except Exception:
                        pass

            if createdComment and createdComment.get("id"):
                entity["taskId"] = ktask["id"]
                self.getMediaVersions(entity, parent=parent, allowCache=False)
                self.getNotes("mediaVersion", entity, allowCache=False)

            url = self.makeDbRequest("task", "get_task_url", ktask)
            data = {"url": url, "versionName": versionName}
            return data

    @err_catcher(name=__name__)
    def onWriteNoteWidgetCreated(self, origin):
        if self.prjMng.curManager != self:
            return

        origin.lo_add = QHBoxLayout()
        origin.b_status = QPushButton()
        origin.b_status.clicked.connect(lambda: self.onNoteStatusClicked(origin))
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        origin.b_addNote.setSizePolicy(sizePolicy)
        origin.lo_add.addWidget(origin.b_addNote)
        origin.lo_add.addWidget(origin.b_status)
        origin.lo_newNote.insertLayout(1, origin.lo_add)
        statusCode = self.core.getConfig("prjManagement", "kitsu_versionPubStatus", config="project") or "wfa"
        statusDict = self.makeDbRequest("task", "get_task_status_by_short_name", statusCode)
        if statusDict:
            statusDict = statusDict.copy()
            statusDict["abbreviation"] = statusDict["short_name"]
            statusDict["color"] = [int(statusDict["color"].lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
            self.setNoteStatus(origin, statusDict)

    @err_catcher(name=__name__)
    def onNoteStatusClicked(self, origin):
        pos = QCursor.pos()
        tmenu = QMenu(origin)
        tmenu.setStyleSheet("QMenu::item#test {background-color: rgb(255,0,0);}")

        status = self.prjMng.getTaskStatusList()
        for stat in status:
            label = stat["abbreviation"].upper()
            if label == origin.b_status.text():
                continue

            tAct = QWidgetAction(origin)
            w_status = QWidget()
            lo_status = QHBoxLayout(w_status)
            lo_status.setContentsMargins(9, 3, 9, 3)
            l_status = QLabel(label)
            lo_status.addWidget(l_status)
            tAct.setDefaultWidget(w_status)
            if stat["color"][0] < 200 or stat["color"][1] < 200 or stat["color"][2] < 200:
                textColor = "white"
            else:
                textColor = "black"

            w_status.setStyleSheet("color: %s;background-color: rgb(%s, %s, %s);" % (textColor, stat["color"][0], stat["color"][1], stat["color"][2]))
            tAct.triggered.connect(lambda x=None, s=stat: self.setNoteStatus(origin, s))
            tmenu.addAction(tAct)

        tmenu.exec_(pos)

    @err_catcher(name=__name__)
    def setNoteStatus(self, origin, status):
        label = status["abbreviation"].upper()
        origin.b_status.setText(label)
        origin.b_status.setToolTip("Status: %s" % label)
        if status["color"][0] < 200 or status["color"][1] < 200 or status["color"][2] < 200:
            textColor = "white"
        else:
            textColor = "black"

        origin.b_status.setStyleSheet("QPushButton { color: %s;background-color: rgb(%s, %s, %s); border: none;}" % (textColor, status["color"][0], status["color"][1], status["color"][2]))

    @err_catcher(name=__name__)
    def getNotes(self, entityType, entity, allowCache=True):
        prjId = self.getCurrentProjectKey()
        if prjId is None:
            return

        if entityType == "task":
            task = entity.get("task", "")
        elif entityType == "productVersion":
            task = entity.get("product", "")
        elif entityType == "mediaVersion":
            task = entity.get("identifier", "")

        takId = entity.get("taskId")
        if not takId:
            takId = entity.get("id")
            if takId is None:
                takId = self.getTaskId(entity, prjId, task)

        self.core.popup(takId)

        rmtNotes = self.makeDbRequest("comments", [], allowCache=allowCache)
        if not rmtNotes:
            return []

        notes = []
        for rmtNote in rmtNotes:
            if entityType in ["productVersion", "mediaVersion"]:
                vdata = self.getVersionFromNoteText(rmtNote.get("text", "-"), entity=entity)
                if not vdata or vdata["version"] != entity.get("version", "") or (vdata["task"] != entity.get("identifier", entity.get("product", ""))):
                    continue

            replies = []
            for reply in rmtNote.get("replies", []):
                dateStr = reply.get("date", "")
                if dateStr:
                    date = datetime.strptime(dateStr, "%Y-%m-%dT%H:%M:%S")
                    if sys.version[0] == "3":
                        timestamp = datetime.timestamp(date)
                    else:
                        timestamp = ((date - datetime(1970, 1, 1)).total_seconds())
                else:
                    timestamp = None

                authorId = reply.get("person_id", {})
                authorData = self.makeDbRequest("person", "get_person", authorId, allowCache=allowCache)
                author = "%s %s" % (authorData.get("first_name", "-"), authorData.get("last_name", "-"))
                data = {
                    "date": timestamp,
                    "author": author,
                    "content": reply.get("text", "-").replace(": ", ":\n"),
                    "id": reply.get("id", ""),
                }
                for attachment in rmtNote.get("attachment_files"):
                    if os.path.splitext(attachment["name"])[0].replace("note_", "") == data["id"]:
                        apath = self.core.getTempFilepath(filename="kitsu/note_attachment_%s.jpg" % data["id"])
                        if not os.path.exists(apath):
                            authorData = self.makeDbRequest("files", "download_attachment_file", [attachment, apath], allowCache=allowCache)

                        data["attachment_path"] = apath

                replies.append(data)

            dateStr = rmtNote.get("created_at", "")
            if dateStr:
                date = datetime.strptime(dateStr, "%Y-%m-%dT%H:%M:%S")
                if sys.version[0] == "3":
                    timestamp = datetime.timestamp(date)
                else:
                    timestamp = ((date - datetime(1970, 1, 1)).total_seconds())
            else:
                timestamp = None

            authorData = rmtNote.get("person") or {}
            author = "%s %s" % (authorData.get("first_name", "-"), authorData.get("last_name", "-"))
            tags = [
                {
                    "label": rmtNote["task_status"]["short_name"].upper(),
                    "color": [int(rmtNote["task_status"]["color"].lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
                }
            ]
            data = {
                "date": timestamp,
                "author": author,
                "content": rmtNote.get("text", "-").replace(": ", ":\n"),
                "replies": replies,
                "id": rmtNote.get("id", ""),
                "tags": tags
            }
            notes.append(data)

        return notes

    @err_catcher(name=__name__)
    def createNote(self, entityType, entity, note, origin):
        prjId = self.getCurrentProjectKey()
        if prjId is None:
            return

        logger.debug("adding note to Kitsu: %s" % note)

        if entityType == "task":
            task = entity.get("task", "")
        elif entityType == "productVersion":
            task = entity.get("product", "")
        elif entityType == "mediaVersion":
            task = entity.get("identifier", "")

        tasks = self.getTasksFromEntity(entity)
        for tsk in tasks:
            if tsk["task"] == task:
                ktask = tsk
                break
        else:
            msg = "Couldn't find matching task in Kitsu. Failed to add note."
            self.core.popup(msg)
            return False

        if origin:
            taskStatusCode = origin.w_newNote.b_status.text().lower()
        else:
            taskStatusCode = ktask.get("status", "")

        statusDict = self.makeDbRequest("task", "get_task_status_by_short_name", taskStatusCode)
        if not statusDict:
            msg = "The status \"%s\" doesn't exist in Kitsu. Publish canceled." % taskStatusCode
            self.core.popup(msg)
            return

        result = self.makeDbRequest("task", "add_comment", [ktask, statusDict, note], allowCache=False)
        if result and result.get("id"):
            self.makeDbRequest("task", "all_comments_for_task", ktask, allowCache=False)
            self.getTasksFromEntity(entity, allowCache=False)
            if self.core.pb:
                self.core.pb.refreshUiTriggered()

            data = {
                "content": note,
                "author": self.core.username,
                "date": time.time(),
                "replies": [],
                "id": result.get("id"),
                "entityType": entityType,
                "entity": entity,
            }
            return data

    @err_catcher(name=__name__)
    def createReply(self, entityType, entity, parentNote, note, origin):
        prjId = self.getCurrentProjectKey()
        if prjId is None:
            return

        logger.debug("adding reply to Kitsu: %s" % note)

        if entityType == "task":
            task = entity.get("task", "")
        elif entityType == "productVersion":
            task = entity.get("product", "")
        elif entityType == "mediaVersion":
            task = entity.get("identifier", "")

        tasks = self.getTasksFromEntity(entity)
        for tsk in tasks:
            if tsk["task"] == task:
                break
        else:
            msg = "Couldn't find matching task in Kitsu. Failed to add reply."
            self.core.popup(msg)
            return False

        comment = {"id": parentNote.get("id")}
        result = self.makeDbRequest("task", "add_reply_to_comment", [tsk, comment, note], allowCache=False)
        if result and result.get("id"):
            self.makeDbRequest("task", "all_comments_for_task", tsk, allowCache=False)
            self.getMediaVersions(entity, allowCache=False)
            data = {
                "content": note,
                "author": self.core.username,
                "date": time.time(),
                "replies": [],
                "id": result.get("id"),
                "entityType": entityType,
                "entity": entity,
                "parentNote": parentNote,
            }
            return data

    @err_catcher(name=__name__)
    def addAttachmentToNote(self, entityType, entity, parentNote, attachment):
        prjId = self.getCurrentProjectKey()
        if prjId is None:
            return

        logger.debug("adding attachment to Kitsu: %s" % attachment)

        if entityType == "task":
            task = entity.get("task", "")
        elif entityType == "productVersion":
            task = entity.get("product", "")
        elif entityType == "mediaVersion":
            task = entity.get("identifier", "")

        tasks = self.getTasksFromEntity(entity)
        for tsk in tasks:
            if tsk["task"] == task:
                break
        else:
            msg = "Couldn't find matching task in Kitsu. Failed to add reply."
            self.core.popup(msg)
            return False

        comment = {"id": parentNote.get("id")}
        result = self.makeDbRequest("task", "add_attachment_files_to_comment", [tsk, comment, attachment], allowCache=False)
        if result:
            self.makeDbRequest("task", "all_comments_for_task", tsk, allowCache=False)
            self.getMediaVersions(entity, allowCache=False)
            return True

    @err_catcher(name=__name__)
    def getVersionFromNoteText(self, text, entity=None):
        data = None
        base = text.split(":")[0]
        if entity:
            if entity.get("type") == "asset":
                entityName = entity.get("asset_path", "").replace("\\", "/")
            elif entity.get("type") == "shot":
                entityName = self.core.entities.getShotName(entity)

            if base.startswith(entityName):
                items = base[len(entityName):].strip("_").split("_")
                if len(items) != 2:
                    return

                data = {
                    "entity": entityName,
                    "task": items[0],
                    "version": items[1],
                }

        else:
            items = base.split("_")
            if len(items) != 3:
                return

            data = {
                "entity": items[0],
                "task": items[1],
                "version": items[2],
            }

        return data

    # TODO : Revisit
    @err_catcher(name=__name__)
    def getAssignedTasks(self, user=None, allowCache=True):
        prjId = self.getCurrentProjectKey()
        if prjId is None:
            return

        if not user:
            user = self.makeDbRequest(self.JIRA, "myself")["displayName"]
            if not user:
                logger.warning("no user specified.")
                return []

        jiraTasks = list(self.makeDbRequest(self.JIRA, "search_issues", [self.makeJqlQuery(f"assignee = '{user}' AND type IN (Task, Sub-task)", priority=True), 0, 0], allowCache=allowCache) or [])
        
        taskStatusList = self.prjMng.getTaskStatusList()

        async def processTask(jiraTask):
            projCmpnts = self.getCurrentComponents()
            taskCmpnts = jiraTask.get_field("components")

            # If this task is assigned to any component that the project uses
            if not any(str(taskCmpnt) in projCmpnts for taskCmpnt in taskCmpnts):
                return

            entityLinks = [x for x in jiraTask.fields.issuelinks if x.type.name=="Entity Link"]
            if len(entityLinks) > 1 :
                logger.warning(f"Jira task {jiraTask.get_field('summary')} has more than one entity link. This is illegal behavior! \nPlease report to production ASAP.")
                return
            if len(entityLinks) == 0: # Not connected to an entity. Shouldn't be displayed.
                return
            else:
                entityIssue = self.JIRA.issue(entityLinks[0].outwardIssue.key)

            if entityIssue.get_field("issuetype").name == "Shot":
                sequenceLinks = [x for x in entityIssue.fields.issuelinks if x.type.name=="Sequence Link"]

                if "_sequence" in entityIssue.get_field("summary"):
                    sequenceName = entityIssue.get_field("summary").split("_sequence")[0]
                elif len(sequenceLinks) == 1:
                    sequenceName = self.JIRA.issue(sequenceLinks[0].outwardIssue.key).get_field("summary")
                else:
                    logger.warning(
                        f"Jira shot {entityIssue.get_field('summary')} has more than one (or zero) sequence links. This is illegal behavior! \nPlease report to production ASAP."
                    )
                    return
                
                sdata = {"shot": entityIssue.get_field("summary"), "sequence": sequenceName}
                path = self.core.entities.getShotName(sdata)
                entity = {"type": "shot", "shot": entityIssue.get_field("summary"), "sequence": sequenceName}
            elif entityIssue.get_field("issuetype").name == "Asset":
                path = "%s/%s" % (re.split(r"\s*-\s*", entityIssue.get_field("parent").get_field("summary"))[-1].replace(" ", "_"), re.split(r"\s*-\s*", entityIssue.get_field("summary"))[-1].replace(" ", "_"))
                entity = {"type": "asset", "asset_path": path}

            if jiraTask.get_field("customfield_10015"): # Start Date
                date = datetime.strptime(jiraTask.get_field("customfield_10015"), "%Y-%m-%d")
                if sys.version[0] == "3":
                    startStamp = datetime.timestamp(date)
                else:
                    startStamp = ((date - datetime(1970, 1, 1)).total_seconds())
            else:
                startStamp = None

            if jiraTask.get_field("duedate"): # End Date
                date = datetime.strptime(jiraTask.get_field("duedate"), "%Y-%m-%d")
                if sys.version[0] == "3":
                    endStamp = datetime.timestamp(date)
                else:
                    endStamp = ((date - datetime(1970, 1, 1)).total_seconds())
            else:
                endStamp = None

            statusname = jiraTask.get_field("status")
            statusData = [status for status in taskStatusList if status["name"].lower() == statusname.lower()]
            if statusData:
                status = statusData[0]["abbreviation"]
            else:
                status = ""

            if jiraTask.get_field("priority") != "To Do":
                # no idea what this does
                # path += " (%s)" % ("!" * rmtTask["priority"])
                pass

            # dep = self.getDepartmentNameByTaskTypeId(rmtTask["task_type_id"])
            # if not dep:
            #     continue

            taskName = re.split(r"\s*-\s*", jiraTask.get_field("summary"))[-1]

            if len(jiraTask.get_field("labels")) != 0:
                dep = jiraTask.get_field("labels")[0]
            else:
                logger.warning(f"Jira task {jiraTask.get_field('summary')} does not have a department label set. \nsPlease report this to production ASAP!")
                dep = "MISSING"

            data = {
                "name": taskName,
                "entity": entity,
                "path": path,
                "department": dep,
                "status": status,
                "start_date": startStamp,
                "end_date": endStamp,
                "id": jiraTask.key,
            }
            return data
        
        tasks = []
        aioTasks = []
        async def processTasks():
            async with asyncio.TaskGroup() as tg:
                for jiraTask in jiraTasks:
                    aioTask = tg.create_task(processTask(jiraTask))
                    aioTasks.append(aioTask)

            results = [aioTask.result() for aioTask in aioTasks]
            for x in results:
                tasks.append(x)

        # asyncio.run(processTasks())

        return tasks
    

    # @err_catcher(name=__name__)
    # def getPlaylists(self, allowCache=True):
    #     prjId = self.getCurrentProjectKey()
    #     if prjId is None:
    #         return

    #     playlists = []
    #     rmtPlaylists = self.makeDbRequest("playlist", "all_playlists_for_project", {"id": prjId}, allowCache=allowCache) or {}
    #     for playlist in rmtPlaylists:
    #         playlist = playlist.copy()
    #         playlist["content"] = self.getContentOfPlaylist(playlist)
    #         playlists.append(playlist)
        
    #     return playlists

    # @err_catcher(name=__name__)
    # def getContentOfPlaylist(self, playlist, allowCache=True):
    #     playlist = self.makeDbRequest("playlist", "get_playlist", playlist, allowCache=allowCache)
    #     content = []
    #     usedIds = []

    #     prefTaskType = None
    #     if playlist["task_type_id"]:
    #         prefTaskType = self.getTaskTypeNameByTaskTypeId(playlist["task_type_id"])

    #     for plEntity in (playlist["shots"] or []):
    #         if plEntity["entity_id"] in usedIds:
    #             continue

    #         if playlist["for_entity"] == "asset":        
    #             entity = self.makeDbRequest("asset", "get_asset", plEntity["entity_id"])
    #             if not entity.get("asset_type", ""):
    #                 assetPath = entity["name"]
    #             else:
    #                 assetPath = "%s/%s" % (entity["asset_type"], entity["name"])

    #             entity["asset_path"] = assetPath
    #         else:
    #             entity = self.makeDbRequest("shot", "get_shot", plEntity["entity_id"])
    #             entity["sequence"] = entity["sequence_name"]
    #             entity["shot"] = entity["name"]

    #         entity["type"] = playlist["for_entity"]
    #         versions = self.getMediaVersions(entity)
    #         if versions:
    #             identifier = sorted([v["kitsuTaskName"] for v in versions])
    #             if prefTaskType in identifier:
    #                 usedIdf = prefTaskType
    #             else:
    #                 usedIdf = identifier[0]

    #             versions = [v for v in versions if v["kitsuTaskName"] == usedIdf]
    #             if versions:
    #                 version = sorted(versions, key=lambda x: x["version"])[-1]
    #                 version.update(entity)
    #                 version["path"] = self.core.mediaProducts.getAovPathFromVersion(version)
    #                 if "name" in version:
    #                     del version["name"]

    #                 content.append(version)

    #         usedIds.append(plEntity["entity_id"])

    #     return content

    # @err_catcher(name=__name__)
    # def createPlaylist(self, playlist):
    #     prjId = self.getCurrentProjectKey()
    #     if prjId is None:
    #         return

    #     # TODO : Figure out how it would be best to do media review / playlists in Jira.
    #     self.core.popup("Media review and playlists are not supported in Jira.")
    #     return 
    #     self.makeDbRequest("playlist", "new_playlist", [{"id": prjId}, playlist["name"]])
    #     self.getPlaylists(allowCache=False)

    # TODO : What is this used for??
    # @err_catcher(name=__name__)
    # def getSequence(self, sequenceName, allowCache=True):
    #     text = "Querying sequences - please wait..."
    #     popup = self.core.waitPopup(self.core, text, parent=None, hidden=True)
    #     with popup:
    #         prjId = self.getCurrentProjectKey()
    #         if prjId is None:
    #             return

    #         rmtSeq = self.makeDbRequest("search_issues", [self.makeJqlQuery(f'parent = {self.getShotsEpicKey()} AND summary ~ "_sequence"')], popup=popup, allowCache=allowCache)
    #         return rmtSeq

    # @err_catcher(name=__name__)
    # def createSequence(self, sequenceName):
    #     # TODO
    #     prjId = self.getCurrentProjectKey()
    #     if prjId is None:
    #         return
        
    #     sequence = self.makeDbRequest("shot", "new_sequence", [{"id": prjId}, sequenceName], allowCache=False)
    #     self.getSequence(sequenceName, allowCache=False)
    #     return sequence

    # @err_catcher(name=__name__)
    # def createShot(self, entity, frameRange=None):
    #     # TODO
    #     prjId = self.getCurrentProjectKey()
    #     if prjId is None:
    #         return

    #     sequence = self.getSequence(entity["sequence"])
    #     if not sequence:
    #         sequence = self.createSequence(entity["sequence"])

    #     args = [{"id": prjId}, sequence, entity["shot"]]
    #     if frameRange:
    #         args += [None, frameRange[0], frameRange[1]]

    #     self.makeDbRequest("shot", "new_shot", args, allowCache=False)
    #     self.getShots(allowCache=False)
    #     result = {
    #         "entity": entity,
    #         "entityPath": self.core.getEntityPath(entity=entity),
    #         "existed": False,
    #     }
    #     logger.debug("shot created: %s" % result)
    #     return result

    # @err_catcher(name=__name__)
    # def createAsset(self, entity):
    #     # TODO

    #     prjId = self.getCurrentProjectKey()
    #     if prjId is None:
    #         return

    #     assetTypeName = os.path.dirname(entity["asset_path"])
    #     self.core.popup(assetTypeName)
    #     # assetType = self.makeDbRequest("asset", "get_asset_type_by_name", assetTypeName, allowCache=False)
    #     # if not assetType:
    #     #     msg = "Assettype \"%s\" doesn't exist.\n\nPlease create it in Kitsu before creating an asset with this type." % os.path.dirname(entity["asset_path"])
    #     #     self.core.popup(msg)
    #     #     return

    #     args = [{"project": prjId}, assetTypeName, os.path.basename(entity["asset_path"])]

    #     try:
    #         self.makeDbRequest("create_issue", args, allowCache=False)
    #     except Exception:
    #         exc_type, exc_obj, exc_tb = sys.exc_info()
    #         erStr = "ERROR:\n%s" % traceback.format_exc()
    #         self.core.popup(erStr, title="Kitsu Asset")
    #         return

    #     self.getAssets(allowCache=False)
    #     result = {
    #         "entity": entity,
    #         "entityPath": self.core.getEntityPath(entity=entity),
    #         "existed": False,
    #     }
    #     logger.debug("asset created: %s" % result)
    #     return result
