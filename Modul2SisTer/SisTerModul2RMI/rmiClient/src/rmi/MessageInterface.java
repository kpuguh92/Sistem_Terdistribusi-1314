/*
 * To change this template, choose Tools | Templates
 * and open the template in the editor.
 */
package rmi;

import java.rmi.Remote;
import java.rmi.RemoteException;
import java.util.List;
/**
 *
 * @author Wildhan Ibrahim
 */
public interface MessageInterface extends Remote{
    void sender(String addr, String request) throws RemoteException;
    List<String> rssReader(String params) throws RemoteException;
}
