package server;
import java.io.*;
import java.net.*;

import messages.Message;


public class MainServer {
	public static void main (String[] Args) {
		ServerSocket serverSocket;
		Socket clientSocket;
		try {			
			serverSocket = new ServerSocket(2222);
			while(true){
				clientSocket = serverSocket.accept();
				Connection conn = new Connection(clientSocket);
			}
		} catch(IOException ex) {
			System.out.println("Error! : "+ex.getMessage());
		} 
	}
}

class Connection extends Thread {
	ObjectInputStream inpStream;
	ObjectOutputStream outStream;
	Socket clientSocket;
	Message message;
	
	FileInputStream inFile;
	BufferedReader buffRead;
	int countRow = 0;
	String dataAll = "";
	
	public Connection(Socket client) {
		try {
			clientSocket = client;
			inpStream = new ObjectInputStream(clientSocket.getInputStream());
			outStream = new ObjectOutputStream(clientSocket.getOutputStream());
			this.start();		
		} catch(IOException ex) {
			System.out.println("Error! : "+ex.getMessage());
		}
	}
	
	@Override
	public void run() {
		
		try {
			message = (Message) inpStream.readObject();
			System.out.println("From "+message.getSender()+" : "+message.getMessage());
			
			inFile = new FileInputStream("Data/data.txt");
			buffRead = new BufferedReader(new InputStreamReader(inFile));
			String tempData = "";
			while((tempData=buffRead.readLine())!=null) {
				dataAll += tempData+"\n";
				countRow++;
			}
			
			String data[] = dataAll.split("\n");
			String replay = "";
			
			if(message.getMessage().equals("all")){
				replay = "\n"+dataAll;
			} else {
				for (int i=0; i<countRow ; i++){
					if (data[i].startsWith(message.getMessage())){
						replay = data[i];
						break;
					} 
				}
				if (replay == ""){
					replay = "Input salah";
				}
			}
			
			outStream.writeObject(new Message("Server",replay));
			outStream.flush();
		} catch (ClassNotFoundException ex) {
			System.out.println("Error! : "+ex.getMessage());
		} catch(IOException ex) {
			System.out.println("Error! : "+ex.getMessage());
		} finally {
			try {
				inpStream.close();
				outStream.close();
				clientSocket.close();
			} catch (IOException ex) {
				System.out.println("Error! : "+ex.getMessage());
			}
		}
	}
}
