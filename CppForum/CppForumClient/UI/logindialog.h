#ifndef LOGINDIALOG_H
#define LOGINDIALOG_H

#include <QDialog>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QString>

class LoginDialog : public QDialog
{
    Q_OBJECT
public:
    LoginDialog(QWidget *parent = 0);

private:
    QLabel *idLabel;
    QLabel *passwordLabel;
    QLineEdit *idEdit;
    QLineEdit *passwordEdit;
    QPushButton *loginButton;
    QPushButton *anonymousLoginButton;
};

#endif // LOGINDIALOG_H
