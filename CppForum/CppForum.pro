#-------------------------------------------------
#
# Project created by QtCreator 2018-10-28T20:47:16
#
#-------------------------------------------------

QT       += core gui sql

greaterThan(QT_MAJOR_VERSION, 4): QT += widgets

QMAKE_CXXFLAGS += /std:c++17

TARGET = CppForum
TEMPLATE = app

# The following define makes your compiler emit warnings if you use
# any feature of Qt which has been marked as deprecated (the exact warnings
# depend on your compiler). Please consult the documentation of the
# deprecated API in order to know how to port your code away from it.
DEFINES += QT_DEPRECATED_WARNINGS

# You can also make your code fail to compile if you use deprecated APIs.
# In order to do so, uncomment the following line.
# You can also select to disable deprecated APIs only up to a certain version of Qt.
#DEFINES += QT_DISABLE_DEPRECATED_BEFORE=0x060000    # disables all the APIs deprecated before Qt 6.0.0


SOURCES += \
    main.cpp \
    mainwindow.cpp \
    User/administrator.cpp \
    User/commonuser.cpp \
    User/moderator.cpp \
    User/user.cpp \
    Forum/board.cpp \
    Forum/comment.cpp \
    Forum/forum.cpp \
    Forum/post.cpp \
    UI/boardsarea.cpp \
    UI/commentsdialog.cpp \
    UI/logindialog.cpp \
    UI/postcomponent.cpp \
    UI/postedit.cpp \
    UI/postsarea.cpp \
    UI/userinfobar.cpp \
    Storage/forumstorage.cpp \
    Storage/userinfostorage.cpp \
    Storage/postsstorage.cpp \
    Storage/commentsstorage.cpp \
    Storage/boardsstorage.cpp

HEADERS += \
    mainwindow.h \
    User/user.h \
    User/commonuser.h \
    User/administrator.h \
    User/moderator.h \
    infrastructure.h \
    Forum/board.h \
    Forum/comment.h \
    Forum/forum.h \
    Forum/post.h \
    UI/boardsarea.h \
    UI/logindialog.h \
    UI/postcomponent.h \
    UI/postsarea.h \
    UI/userinfobar.h \
    UI/commentsdialog.h \
    UI/postedit.h \
    forumui.h \
    Storage/forumstorage.h \
    Storage/userinfostorage.h \
    Storage/postsstorage.h \
    Storage/commentsstorage.h \
    Storage/boardsstorage.h
